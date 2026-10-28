"""Local release recovery and authenticated candidate verification regressions."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
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
        with patch.object(local, 'ROOT', self.root), patch.object(control, 'api', side_effect=release), patch.object(local, 'run') as command:
            first = local.stage_candidate(stages, value, commit)
            (source / 'checksums.json').touch()  # Collecting the same bytes changes filesystem timestamps.
            second = local.stage_candidate(stages, value, commit)
        self.assertEqual(first, second)
        command.assert_not_called()

    def test_failed_candidate_import_retries_the_same_run_without_dispatching_a_build(self):
        with patch.object(control, 'ROOT', self.root):
            state_path = control.start_state('1.9.6', 'a' * 40)
        fields = {'version': '1.9.6', 'local_candidate': '20', 'candidate_sha256': 'b' * 64}
        state = json.loads(state_path.read_text())
        state['stages']['build'] = {'workflow': 'build-windows-release.yml', 'fields': fields, 'runId': 123}
        control.save(state_path, state)
        with patch.object(control, 'command') as command, patch.object(control, 'api', return_value={'run_attempt': 2}), \
             patch.object(control, 'wait_run', side_effect=[{'conclusion': 'failure', 'run_attempt': 1}, {'conclusion': 'success', 'run_attempt': 2}]):
            self.assertEqual(control.workflow(state_path, 'build', 'build-windows-release.yml', fields, retry_failed=True), 123)
        self.assertEqual(command.call_args.args[0], ['gh', 'run', 'rerun', '123', '--failed', '--repo', control.REPO])


if __name__ == '__main__':
    unittest.main()
