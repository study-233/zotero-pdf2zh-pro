"""Local release recovery and authenticated candidate verification regressions."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
import zipfile

import release_control as control
import release_local as local
import test_release_control as control_tests


class LocalReleaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_failed_test_resumes_without_rebuilding_unchanged_binary(self):
        binary = self.root / 'app.exe'
        state = self.root / 'stages.json'
        build = Mock(side_effect=lambda: binary.write_bytes(b'compiled'))
        stages = local.Stages(state, {'commit': 'a' * 40})
        stages.execute('build', build, [binary])
        with self.assertRaises(RuntimeError):
            stages.execute('test', Mock(side_effect=RuntimeError('timeout')), artifacts={'binary': 'hash'})
        stages = local.Stages(state, {'commit': 'a' * 40})
        stages.execute('build', build, [binary])
        retry = Mock()
        stages.execute('test', retry, artifacts={'binary': 'hash'})
        build.assert_called_once()
        retry.assert_called_once()
        stages.execute('test', retry, artifacts={'binary': 'hash'})
        retry.assert_called_once()
        stages.execute('test', retry, artifacts={'binary': 'different'})
        self.assertEqual(retry.call_count, 2)
        binary.write_bytes(b'altered')
        stages.execute('build', build, [binary])
        self.assertEqual(build.call_count, 2)
        with self.assertRaisesRegex(ValueError, 'changed'):
            local.Stages(state, {'commit': 'b' * 40})

    def test_completed_validation_never_prepares_cache_or_creates_a_test_workspace(self):
        stages = local.Stages(self.root / 'stages.json', {'commit': 'a' * 40})
        artifacts = {'wheel': 'verified'}
        cases = self.root / 'local-upgrades.json'
        cases.write_text('{"cases": []}')
        for name in ('runtime', 'lifecycle', 'progress', 'upgrades'):
            stages.execute(name, lambda: None, [cases] if name == 'upgrades' else [], artifacts)
        with patch.object(local, 'prepare_downloads') as prepare, patch.object(local, 'Workspace') as workspace, \
             patch.object(local, 'run') as execute:
            local.validate_local(stages, {}, {}, self.root / 'plan.json', artifacts, '1.9.7', 'a' * 40,
                                 self.root / 'app.zip', self.root / 'setup.exe', self.root / 'app.whl')
        prepare.assert_not_called()
        workspace.assert_not_called()
        execute.assert_not_called()

    def test_timing_preserves_failure_and_records_success_and_failure(self):
        from workspace import measure
        with patch.dict('os.environ', PDF2ZH_RELEASE_METRICS_DIR=str(self.root / 'metrics')):
            with measure('success'):
                pass
            with self.assertRaisesRegex(RuntimeError, 'original failure'):
                with measure('failure'):
                    raise RuntimeError('original failure')
        rows = [json.loads(line) for file in (self.root / 'metrics').glob('*.jsonl') for line in file.read_text().splitlines()]
        self.assertEqual([(r['operation'], r['status']) for r in rows], [('success', 'passed'), ('failure', 'failed')])
        self.assertTrue(all(r['seconds'] >= 0 for r in rows))

    def test_progress_only_recovery_does_not_prepare_installation_downloads(self):
        stages = local.Stages(self.root / 'stages.json', {'commit': 'a' * 40})
        artifacts = {'wheel': 'verified'}
        cases = self.root / 'local-upgrades.json'
        cases.write_text('{"cases": []}')
        for name in ('runtime', 'lifecycle', 'upgrades'):
            stages.execute(name, lambda: None, [cases] if name == 'upgrades' else [], artifacts)
        package = self.root / 'package.zip'
        with zipfile.ZipFile(package, 'w') as archive:
            archive.writestr('zotero-pdf2zh-pro.exe', b'fixture')
        with patch.object(local, 'ROOT', self.root), patch.object(local, 'prepare_downloads') as prepare, \
             patch.object(local, 'run') as execute, patch.object(control, 'command', return_value=b'python.exe'):
            local.validate_local(stages, {}, {'lifecycle': 'standard'}, self.root / 'plan.json', artifacts,
                                 '1.9.7', 'a' * 40, package, self.root / 'setup.exe', self.root / 'app.whl')
        prepare.assert_not_called()
        execute.assert_called_once()
        self.assertTrue(stages.reusable('progress', artifacts=artifacts))

    def test_install_cache_contains_downloads_and_python_without_a_service_environment(self):
        source = self.root / 'user-cache'
        (source / 'archive-v0/package').mkdir(parents=True)
        (source / 'archive-v0/package/demo-1.0.dist-info').mkdir()
        (source / 'archive-v0/package/data').write_bytes(b'verified wheel bytes')
        (source / 'archive-v0/unrelated/other-2.0.dist-info').mkdir(parents=True)
        (source / 'archive-v0/unrelated/data').write_bytes(b'unrelated bytes')
        (source / '.lock').touch()
        package = self.root / 'package'
        package.mkdir()
        requirements = b'demo==1.0 --hash=sha256:fixture\n'
        (package / 'windows-requirements.txt').write_bytes(requirements)
        (package / 'windows-dependencies.json').write_text(json.dumps({
            'python': '3.13.15', 'requirementsSha256': hashlib.sha256(requirements).hexdigest()}))
        uv = self.root / 'uv.exe'
        uv.write_bytes(b'uv')
        python_root = self.root / 'user-python'
        python = python_root / 'cpython-3.13.15-windows-x86_64-none'
        python.mkdir(parents=True)
        (python / 'python.exe').write_bytes(b'python')
        (python_root / 'cpython-3.13.14-windows-x86_64-none').mkdir()
        environment = {}
        def populate(arguments, env):
            if arguments[:3] == ['uv', 'pip', 'install']:
                destination = Path(env['UV_CACHE_DIR']) / 'archive-v0/package/demo-1.0.dist-info'
                destination.mkdir(parents=True, exist_ok=True)
                (destination.parent / 'data').write_bytes(b'verified wheel bytes')
        with patch.object(local, 'ROOT', self.root), patch.object(local.shutil, 'which', return_value=str(uv)), \
             patch.object(local.platform, 'python_version', return_value='3.13.0'), \
             patch.object(control, 'command', return_value=str(python / 'python.exe').encode()), \
             patch.object(local, 'run', side_effect=populate) as run:
            cache = local.prepare_downloads(environment, package=package)
            self.assertEqual(local.prepare_downloads(environment, package=package), cache)
            self.assertEqual(run.call_count, 2)
        self.assertEqual((cache / 'cache/archive-v0/package/data').read_bytes(), b'verified wheel bytes')
        self.assertTrue((cache / 'runtime/python' / python.name / 'python.exe').is_file())
        self.assertFalse((cache / 'cache/.lock').exists())
        self.assertFalse((cache / 'cache/archive-v0/unrelated').exists())
        self.assertFalse((cache / 'runtime/versions').exists())
        self.assertEqual([p.name for p in (cache / 'runtime/python').iterdir()], [python.name])
        self.assertEqual(environment['PDF2ZH_TEST_DOWNLOAD_CACHE'], str(cache))
        with patch.object(local, 'ROOT', self.root), self.assertRaises(ValueError):
            local.prepare_downloads({'PDF2ZH_TEST_DOWNLOAD_CACHE': str(source)}, package=package)

    def test_download_spec_rejects_hash_mismatch_before_installing(self):
        package = self.root / 'package'
        package.mkdir()
        (package / 'windows-requirements.txt').write_bytes(b'changed dependency')
        (package / 'windows-dependencies.json').write_text(json.dumps({'python': '3.13.15', 'requirementsSha256': '0' * 64}))
        with self.assertRaisesRegex(ValueError, 'differ'):
            local.download_spec(package)

    def test_incremental_cache_reuses_unchanged_bytes_and_cleans_only_changed_packages(self):
        base = self.root / '.tmp/cache/downloads'
        source = base / 'sets/source'
        target = base / 'sets/target'
        file = source / 'cache/archive-v0/unchanged/package.py'
        file.parent.mkdir(parents=True)
        file.write_bytes(b'immutable unchanged package')
        identity = {'uvSha256': '1' * 64, 'platform': 'windows-x86_64', 'requirements': [['3.13.15', 'new']]}
        (source / 'ready.json').write_text(json.dumps({**identity, 'requirements': [['3.13.15', 'old']]}))
        (source / 'packages.json').write_text(json.dumps({'demo': ['1.0'], 'unchanged': ['1.0']}))
        target.mkdir(parents=True)
        environment = {'UV_CACHE_DIR': str(target / 'cache')}
        with patch.object(local, 'ROOT', self.root), patch.object(local, 'run') as run:
            local.reuse_downloads(base, target, identity, {'demo': ['2.0'], 'unchanged': ['1.0']}, environment)
        run.assert_called_once_with(['uv', 'cache', 'clean', 'demo'], environment)
        self.assertTrue(file.samefile(target / 'cache/archive-v0/unchanged/package.py'))
        self.assertEqual(file.read_bytes(), b'immutable unchanged package')

    def test_download_set_writer_lock_is_exclusive_and_released_after_failure(self):
        lock = self.root / 'cache.lock'
        with self.assertRaisesRegex(RuntimeError, 'original failure'):
            with local.download_cache_lock(lock):
                with self.assertRaises(OSError):
                    with local.download_cache_lock(lock):
                        self.fail('Concurrent cache writers were accepted')
                raise RuntimeError('original failure')
        with local.download_cache_lock(lock):
            pass

    def test_seeded_install_removal_preserves_download_cache_and_rejects_overlapping_paths(self):
        cache = self.root / '.tmp/cache/downloads'
        root = self.root / '.tmp/runs/install'
        file = cache / 'cache/archive-v0/id/package.py'
        file.parent.mkdir(parents=True)
        file.write_bytes(b'immutable package')
        ignored = cache / 'runtime/python/.temp/unfinished'
        ignored.parent.mkdir(parents=True)
        ignored.write_bytes(b'unfinished download')
        with patch.object(local, 'ROOT', self.root):
            local.seed_install(cache, root)
            installed = root / 'cache/archive-v0/id/package.py'
            self.assertTrue(file.samefile(installed))
            self.assertFalse((root / 'runtime/python/.temp').exists())
            installed.unlink()
            self.assertEqual(file.read_bytes(), b'immutable package')
            with self.assertRaises(ValueError): local.seed_install(cache, cache / 'install')
            with self.assertRaises(ValueError): local.seed_install(cache, self.root / 'outside')

    def receipt(self):
        plan = control_tests.PolicyTests().frozen_plan()
        artifacts = {Path(p).name: {'size': 1, 'sha256': '1' * 64} for p in control.product_paths(plan['version'])}
        cases = []
        for case in plan['cases']:
            old = plan['baselines'][case['baseline']]
            cases.append({**case, 'receipt': {'status': 'passed', 'version': plan['version'], 'previousVersion': case['baseline'],
                'commit': plan['sourceCommit'], 'planSha256': control.plan_digest(plan), 'harnessSha256': plan['harnessSha256'],
                'packageSha256': artifacts[control.PACKAGE]['sha256'],
                'wheelSha256': artifacts[f'zotero_pdf2zh_pro-{plan["version"]}-py3-none-any.whl']['sha256'],
                'previousPackageSha256': old['package']['sha256'], 'previousWheelSha256': old['wheel']['sha256']}})
        receipt = {'schemaVersion': 2, 'status': 'passed', 'plan': plan, 'planSha256': control.plan_digest(plan),
            'lifecycle': plan['lifecycle'], 'artifacts': artifacts, 'cases': cases,
            'execution': {'type': 'local', 'id': 'a' * 32, 'system': 'Windows', 'python': '3.13', 'rustc': 'rustc 1.95'},
            'stages': {name: {'status': 'passed', 'artifacts': artifacts} for name in
                       ('native-tests', 'package-window', 'runtime', 'lifecycle', 'progress', 'upgrades')}}
        return plan, artifacts, receipt

    def test_local_receipt_requires_environment_every_stage_and_selected_upgrade_evidence(self):
        plan, artifacts, receipt = self.receipt()
        control.verify_standard_receipt(receipt, plan, artifacts)
        mutations = [lambda r: r['execution'].update(system='Linux'), lambda r: r['execution'].update(id=''),
                     lambda r: r['stages'].pop('runtime'), lambda r: r['stages']['lifecycle'].update(status='failed'),
                     lambda r: r['stages']['native-tests'].update(artifacts={}), lambda r: r.update(cases=[]),
                     lambda r: r['cases'][0]['receipt'].update(previousWheelSha256='2' * 64)]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                changed = copy.deepcopy(receipt)
                mutate(changed)
                with self.assertRaises(ValueError):
                    control.verify_standard_receipt(changed, plan, artifacts)

    def test_import_rejects_traversal_duplicate_and_changed_bytes_before_package_execution(self):
        value, commit = '1.9.6', 'a' * 40
        expected = {Path(p).name for p in control.product_paths(value)} | {'checksums.json', 'validation-plan.json', 'standard-validation.json'}
        for variant in ('traversal', 'duplicate', 'bytes'):
            with self.subTest(variant=variant):
                destination = self.root / variant / 'received'
                destination.parent.mkdir()
                bundle = destination.parent / 'local-candidate.zip'
                with zipfile.ZipFile(bundle, 'w') as archive:
                    for name in sorted(expected):
                        if name == 'checksums.json':
                            payload = json.dumps({'version': value, 'commit': commit,
                                'artifacts': {'update.json': {'size': 1, 'sha256': 'f' * 64}}})
                        else:
                            payload = '{}'
                        archive.writestr(name, payload)
                    if variant == 'traversal': archive.writestr('../escaped', 'bad')
                    if variant == 'duplicate': archive.writestr('update.json', 'duplicate')
                sha = control.digest(bundle)
                with patch.object(control, 'api', return_value={'name': bundle.name, 'digest': 'sha256:' + sha}), \
                     patch.object(control, 'download'), patch.object(control, 'verify_publication_contract'), \
                     patch.object(local, 'run') as execute:
                    with self.assertRaises(ValueError):
                        local.import_candidate('123', sha, value, commit, destination)
                    execute.assert_not_called()
                self.assertFalse((destination.parent / 'escaped').exists())

    def test_candidate_resume_keeps_identical_archive_without_uploading_again(self):
        value, commit = '1.9.6', 'a' * 40
        source = self.root / 'dist/release'
        source.mkdir(parents=True)
        names = {Path(p).name for p in control.product_paths(value)} | {'checksums.json', 'validation-plan.json', 'standard-validation.json'}
        for name in names:
            (source / name).write_bytes(name.encode())
        stages = local.Stages(self.root / 'local-build.json', {'commit': commit})
        def release(_):
            bundle = self.root / 'local-candidate.zip'
            return {'id': 10, 'draft': True, 'target_commitish': commit,
                    'assets': [{'id': 20, 'name': bundle.name, 'digest': 'sha256:' + control.digest(bundle)}]}
        with patch.object(local, 'ROOT', self.root), patch.object(local, 'candidate_release', side_effect=lambda *args: release(None)), patch.object(local, 'run') as command:
            first = local.stage_candidate(stages, value, commit)
            (source / 'checksums.json').touch()  # Collecting the same bytes changes filesystem timestamps.
            second = local.stage_candidate(stages, value, commit)
        self.assertEqual(first, second)
        command.assert_not_called()

    def test_draft_resolution_uses_authenticated_list_and_then_the_release_id(self):
        draft = {'id': 42, 'tag_name': 'candidate', 'draft': True}
        with patch.object(control, 'command', return_value=json.dumps([[draft]]).encode()) as command:
            self.assertEqual(local.candidate_release('candidate'), draft)
            self.assertNotIn('/tags/', command.call_args.args[0][-1])
        with patch.object(control, 'api', return_value=draft) as api:
            self.assertEqual(local.candidate_release('candidate', 42), draft)
        self.assertTrue(api.call_args.args[0].endswith('/releases/42'))

    def test_failed_candidate_import_renews_transport_without_rebuilding(self):
        with patch.object(control, 'ROOT', self.root):
            state_path = control.start_state('1.9.6', 'a' * 40)
        fields = {'version': '1.9.6', 'local_candidate': '20', 'candidate_sha256': 'b' * 64, 'candidate_url': 'old'}
        state = json.loads(state_path.read_text())
        state['stages']['build'] = {'workflow': 'build-windows-release.yml', 'fields': fields, 'runId': 123}
        control.save(state_path, state)
        run = {'id':124, 'head_sha':'a'*40, 'run_attempt':1, 'display_title':'Build ['+'a'*32+']'}
        with patch.object(control, 'command') as command, patch.object(control, 'api', return_value={'workflow_runs':[run]}), \
             patch.object(control.uuid, 'uuid4') as uuid, \
             patch.object(control, 'wait_run', side_effect=[{'conclusion': 'failure', 'run_attempt': 1}, {'conclusion': 'success', 'run_attempt': 1}]):
            uuid.return_value.hex = 'a'*32
            self.assertEqual(control.workflow(state_path, 'build', 'build-windows-release.yml', dict(fields,candidate_url='new'), retry_failed=True), 124)
        self.assertEqual(command.call_args.args[0][:4], ['gh', 'workflow', 'run', 'build-windows-release.yml'])
        self.assertIn('candidate_url=new', command.call_args.args[0])
        self.assertIn('local_candidate=20', command.call_args.args[0])

    def test_signed_transport_keeps_credentials_local_and_checks_download_hash(self):
        url='https://release-assets.githubusercontent.com/github-production-release-asset/123?token=temporary'
        redirect=HTTPError('https://api.github.com/asset',302,'redirect',{'Location':url},None)
        with patch.object(control,'command',return_value=b'fixture-token'), patch.object(local,'build_opener') as opener:
            opener.return_value.open.side_effect=redirect
            self.assertEqual(local.signed_candidate_url('123'),url)
            request=opener.return_value.open.call_args.args[0]
            self.assertTrue(request.full_url.startswith('https://api.github.com/'))
        for invalid in ('http://127.0.0.1/x','file:///x','https://evil.example/x','https://user@release-assets.githubusercontent.com/x'):
            with self.assertRaises(ValueError): local.validate_candidate_url(invalid)
        with patch.object(control,'api') as api, patch.object(control,'download',side_effect=lambda args,path: Path(path).write_bytes(b'wrong bytes')):
            with self.assertRaisesRegex(ValueError,'digest mismatch'):
                local.import_candidate('123','a'*64,'1.9.6','b'*40,self.root/'import',url)
        api.assert_not_called()


if __name__ == '__main__':
    unittest.main()
