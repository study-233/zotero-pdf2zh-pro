"""Offline behavioral tests for publication, recovery and compatibility selection."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

import release_control as control
import release_policy as policy

SHA = 'a' * 40
ROOT = Path(__file__).resolve().parents[1]


class PublicationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.files = [self.root / name for name in ('package.zip', 'update.json')]
        for file in self.files:
            file.write_bytes(file.name.encode())

    def asset(self, file, **changes):
        return dict(name=file.name, id=1, size=file.stat().st_size,
                    digest='sha256:' + control.digest(file), **changes)

    def test_retry_keeps_matching_assets_and_uploads_only_missing_files(self):
        with patch.object(control, 'api', return_value={'assets': [self.asset(self.files[0])]}), \
             patch.object(control, 'command') as command:
            control.safe_publish('owner/repo', 'v2.0.0', SHA, self.root/'notes', self.files)
        args = command.call_args.args[0]
        self.assertEqual(args[:4], ['gh', 'release', 'upload', 'v2.0.0'])
        self.assertIn(str(self.files[1]), args)
        self.assertNotIn(str(self.files[0]), args)
        self.assertNotIn('--clobber', args)

    def test_conflict_in_last_asset_prevents_uploading_missing_first_asset(self):
        asset = self.asset(self.files[1]); asset['size'] += 1
        with patch.object(control, 'api', return_value={'assets': [asset]}), patch.object(control, 'command') as command:
            with self.assertRaisesRegex(ValueError, 'conflicts'):
                control.safe_publish('owner/repo', 'v2.0.0', SHA, self.root/'notes', self.files)
        command.assert_not_called()

    def test_hash_conflict_is_rejected_even_with_matching_size(self):
        other = self.root/'other'; other.write_bytes(b'x' * self.files[0].stat().st_size)
        with patch.object(control, 'api', return_value={'assets': [self.asset(self.files[0])]}), \
             patch.object(control, 'command') as command, patch.object(control, 'cached_asset', return_value=other):
            asset = self.asset(self.files[0]); asset['digest'] = 'sha256:' + control.digest(other)
            with patch.object(control, 'api', return_value={'assets': [asset]}):
                with self.assertRaisesRegex(ValueError, 'conflicts'):
                    control.safe_publish('owner/repo', 'v2.0.0', SHA, self.root/'notes', self.files)
        command.assert_not_called()

    def test_network_error_is_not_treated_as_an_absent_release(self):
        with patch.object(control, 'api', side_effect=RuntimeError('HTTP 503')), patch.object(control, 'command') as command:
            with self.assertRaises(RuntimeError):
                control.safe_publish('owner/repo', 'v2.0.0', SHA, self.root/'notes', self.files)
        command.assert_not_called()

    def test_existing_tag_uses_verify_tag_without_an_unnecessary_target(self):
        with patch.object(control, 'api', side_effect=RuntimeError('HTTP 404')), \
             patch.object(control, 'tag_commit', return_value=SHA), patch.object(control, 'command') as command:
            control.safe_publish('owner/repo','v2.0.0',SHA,self.root/'notes',self.files)
        args=command.call_args.args[0]
        self.assertIn('--verify-tag',args)
        self.assertNotIn('--target',args)

    def test_missing_tag_uses_the_exact_source_commit(self):
        with patch.object(control, 'api', side_effect=RuntimeError('HTTP 404')), \
             patch.object(control, 'tag_commit', side_effect=RuntimeError('HTTP 404')), \
             patch.object(control, 'command') as command:
            control.safe_publish('owner/repo','v2.0.0',SHA,self.root/'notes',self.files)
        args=command.call_args.args[0]
        self.assertEqual(args[args.index('--target')+1],SHA)

    def test_conflicting_tag_prevents_release_creation(self):
        with patch.object(control, 'api', side_effect=RuntimeError('HTTP 404')), \
             patch.object(control, 'tag_commit', return_value='b'*40), patch.object(control, 'command') as command:
            with self.assertRaisesRegex(ValueError,'tag conflicts'):
                control.safe_publish('owner/repo','v2.0.0',SHA,self.root/'notes',self.files)
        command.assert_not_called()

    def test_tag_lookup_failure_is_not_treated_as_a_missing_tag(self):
        with patch.object(control, 'api', side_effect=RuntimeError('HTTP 404')), \
             patch.object(control, 'tag_commit', side_effect=RuntimeError('HTTP 503')), \
             patch.object(control, 'command') as command:
            with self.assertRaises(RuntimeError):
                control.safe_publish('owner/repo','v2.0.0',SHA,self.root/'notes',self.files)
        command.assert_not_called()


class PolicyTests(unittest.TestCase):
    def test_pinned_wheel_cache_avoids_another_download(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);package=root/'package.zip';package.write_bytes(b'zip')
            payload=b'published wheel';sha=hashlib.sha256(payload).hexdigest()
            cached=root/'.tmp/cache/release-baselines'/sha
            cached.parent.mkdir(parents=True);cached.write_bytes(payload)
            baseline={'package':{'sha256':'a'*64},'wheel':{'name':'zotero_pdf2zh_pro-1.9.1-py3-none-any.whl',
                'sha256':sha,'size':len(payload),'url':'https://files.pythonhosted.org/packages/fixture.whl'}}
            with patch.object(control,'ROOT',root),patch.object(control,'cached_asset',return_value=package),patch.object(control,'download') as download:
                result=control.fetch_baseline({'baselines':{'1.9.1':baseline}},'1.9.1',root/'download')
            self.assertEqual(Path(result['wheel']).read_bytes(),payload)
            download.assert_not_called()

    def test_legacy_uv_reuses_only_matching_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); original=root/'uv.exe';original.write_bytes(b'pinned fixture')
            with patch.object(control,'policy',return_value={'legacyUv':{'version':'0.12.10','sha256':control.digest(original)}}), \
                 patch.object(control.shutil,'which',return_value=str(original)), patch.object(control,'download') as download:
                control.legacy_uv(root/'private/uv.exe')
            self.assertEqual((root/'private/uv.exe').read_bytes(),original.read_bytes())
            download.assert_not_called()

    def test_legacy_uv_rejects_a_changed_official_download(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            def download(args,destination):
                with zipfile.ZipFile(destination,'w') as zipped:zipped.writestr('uv.exe',b'wrong bytes')
            with patch.object(control,'ROOT',root),patch.object(control.shutil,'which',return_value=None), \
                 patch.object(control,'download',side_effect=download):
                with self.assertRaisesRegex(ValueError,'identity mismatch'):
                    control.legacy_uv(root/'private/uv.exe')
            self.assertFalse((root/'private/uv.exe').exists())

    def test_policy_identity_survives_checkout_line_endings(self):
        with tempfile.TemporaryDirectory() as directory:
            file=Path(directory)/'policy.json'
            text=policy.POLICY_PATH.read_text(encoding='utf-8')
            file.write_bytes(text.replace('\r\n','\n').encode())
            with patch.object(policy,'POLICY_PATH',file):
                first=policy.policy_digest()
                file.write_bytes(text.replace('\r\n','\n').replace('\n','\r\n').encode())
                self.assertEqual(first,policy.policy_digest())

    def test_compatibility_status_does_not_hide_network_failures(self):
        with patch.object(control,'api',side_effect=RuntimeError('HTTP 404')):
            self.assertIsNone(control.compatibility_result())
        with patch.object(control,'api',side_effect=RuntimeError('HTTP 503')):
            with self.assertRaises(RuntimeError): control.compatibility_result()
        run={'id':1,'head_sha':SHA,'conclusion':'failure'}
        with patch.object(control,'api',return_value={'workflow_runs':[run]}):
            self.assertEqual(control.compatibility_result(),run)

    def frozen_plan(self):
        result = policy.select([], '1.9.1', '1.9.2')
        result.update(sourceCommit=SHA, harnessSha256='9'*64, baselines={'1.9.1': {
            'version':'1.9.1', 'commit':'b'*40,
            'package':{'id':1,'size':1,'sha256':'c'*64},
            'wheel':{'name':'zotero_pdf2zh_pro-1.9.1-py3-none-any.whl','size':1,'sha256':'d'*64,
                     'url':'https://files.pythonhosted.org/packages/fixture.whl'}}})
        return result

    def test_frozen_plan_cannot_remove_required_checks_or_hide_risky_changes(self):
        with patch.object(control, 'command', return_value=('b'*40).encode()), \
             patch.object(control, 'changed_paths', return_value=[]) as changes:
            plan = self.frozen_plan()
            control.validate_plan(plan)
            plan['cases'] = []
            with self.assertRaisesRegex(ValueError, 'coverage'):
                control.validate_plan(plan)
            plan = self.frozen_plan()
            changes.return_value = ['scripts/windows/runtime.ps1']
            with self.assertRaisesRegex(ValueError, 'coverage'):
                control.validate_plan(plan)

    def test_recovery_rejects_an_old_test_harness_even_with_matching_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            file=Path(directory)/'plan.json'
            control.save(file,self.frozen_plan())
            with patch.object(control,'validate_plan'),patch.object(control,'harness_digest',return_value='8'*64):
                with self.assertRaisesRegex(ValueError,'harness changed'):
                    control.make_plan('1.9.2',SHA,file)

    def test_deferred_validation_requires_matching_cases_and_artifact_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = self.frozen_plan()
            plan['fullValidation'] = True
            plan['cases'].append({'id':'legacy-upgrade','baseline':'1.8.2'})
            plan['cases'].append({'id':'oldest-upgrade','baseline':'1.9.0'})
            control.save(root/'validation-plan.json', plan)
            manifest = {'version':'1.9.2','commit':SHA,'validationPlanSha256':control.plan_digest(plan),
                        'deferredExtendedValidation':True, 'artifacts':{
                            Path(name).name:{'size':1,'sha256':'e'*64} for name in control.product_paths('1.9.2')}}
            manifest['artifacts']['zotero_pdf2zh_pro-1.9.2-py3-none-any.whl']['sha256']='f'*64
            standard={'schemaVersion':1,'status':'passed','plan':plan,'planSha256':control.plan_digest(plan),
                      'lifecycle':plan['lifecycle'],'runId':'123','runAttempt':1,'artifacts':manifest['artifacts']}
            manifest['standardValidationSha256']=control.plan_digest(standard)
            control.save(root/'standard-validation.json',standard)
            control.save(root/'checksums.json',manifest)
            receipt = {'status':'passed','commit':SHA,'planSha256':control.plan_digest(plan),'lifecycle':False,
                       'cases':[{'id':'legacy-upgrade','baseline':'1.8.2','status':'passed'},
                                {'id':'oldest-upgrade','baseline':'1.9.0','status':'passed'}],
                       'packageSha256':'e'*64,'wheelSha256':'f'*64,'harnessSha256':'9'*64}
            with patch.object(control,'validate_plan'):
                control.verify_publication_contract(root,receipt)
                for change in ({'cases':[]},{'packageSha256':'0'*64},{'planSha256':'0'*64},{'harnessSha256':'0'*64}):
                    with self.assertRaises(ValueError):
                        control.verify_publication_contract(root,dict(receipt,**change))
                waiver = {'id':'legacy-upgrade','baseline':'1.8.2','reason':'Explicit user request'}
                waived_receipt = dict(receipt, cases=[receipt['cases'][1]], waivedCases=[waiver])
                control.verify_publication_contract(root,waived_receipt)
                for change in ({'cases':[]},{'packageSha256':'0'*64},
                               {'waivedCases':[dict(waiver,reason='')]},
                               {'waivedCases':[dict(waiver,id='oldest-upgrade',baseline='1.9.0')]}):
                    with self.assertRaises(ValueError):
                        control.verify_publication_contract(root,dict(waived_receipt,**change))

    def test_patch_uses_recent_upgrade_and_risky_change_adds_representatives(self):
        normal = policy.select(['server/chatgpt_client.py'], '1.9.1', '1.9.2')
        self.assertFalse(normal['fullValidation'])
        self.assertEqual(normal['cases'], [{'id': 'recent-upgrade', 'baseline': '1.9.1'}])
        risky = policy.select(['scripts/windows/runtime.ps1'], '1.9.1', '1.9.2')
        self.assertTrue(risky['extendedValidation'])
        self.assertFalse(risky['fullValidation'])
        self.assertEqual(risky['lifecycle'], 'extended')
        self.assertEqual({c['baseline'] for c in risky['cases']}, {'1.9.1', '1.9.0', '1.8.2'})

    def test_unknown_changes_are_conservative_and_duplicate_baselines_are_removed(self):
        plan = policy.select(['a-new-platform/installer'], '1.9.0', '1.9.2')
        self.assertTrue(plan['extendedValidation'])
        self.assertEqual(sum(c['baseline'] == '1.9.0' for c in plan['cases']), 1)

    def test_latest_incomplete_release_cannot_silently_fall_back(self):
        def release(tag, **extra):
            return dict(tag_name=tag, draft=False, prerelease=False, assets=[{'name': control.PACKAGE}, {'name':'windows-update.json'}], **extra)
        older, latest = release('v1.8.2'), release('v1.9.1')
        candidate = release('v1.9.2')
        self.assertEqual(policy.previous_release([candidate, older, latest], '1.9.2'), latest)
        latest['assets'] = []
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            policy.previous_release([older, latest], '1.9.2')

    def test_harness_policy_has_real_entries_and_registered_scenarios(self):
        root = Path(__file__).resolve().parents[1]
        data = policy.policy()
        self.assertEqual(len(data['harnessPaths']), len(set(data['harnessPaths'])))
        for name in data['harnessPaths']:
            self.assertTrue((root/name).is_file(), name)
        for case in data['scenarios'].values():
            self.assertTrue((root/case['entry']).is_file())


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        patched = patch.object(control, 'ROOT', self.root)
        patched.start(); self.addCleanup(patched.stop)
        self.state = control.start_state('1.9.2', SHA)

    def test_resume_requires_same_source_and_active_record_is_retained(self):
        self.assertEqual(control.start_state('1.9.2', SHA, self.state), self.state)
        self.assertTrue((self.state.parent/'.keep').is_file())
        with self.assertRaises(ValueError):
            control.start_state('1.9.2', 'b'*40, self.state)

    def test_completed_channel_record_preserves_build_identity(self):
        control.record_stage(self.state,'core',{'runId':'42','status':'passed'})
        control.record_stage(self.state,'complete',{'githubRelease':'1'})
        result=json.loads(self.state.read_text())
        self.assertEqual(result['status'],'complete')
        self.assertEqual(result['sourceCommit'],SHA)
        self.assertEqual(result['stages']['core']['runId'],'42')

    def test_unknown_dispatch_outcome_is_rediscovered_without_another_dispatch(self):
        fields = {'version':'1.9.2','commit':SHA}
        with patch.object(control, 'command', side_effect=TimeoutError('connection lost')):
            with self.assertRaises(TimeoutError):
                control.workflow(self.state, 'build', 'build-windows-release.yml', fields)
        saved = json.loads(self.state.read_text())['stages']['build']
        run = {'id': 123, 'display_title': f'Build v1.9.2 at {SHA} [{saved["dispatchId"]}]',
               'head_sha':'b'*40, 'run_attempt':1}
        with patch.object(control, 'command') as command, patch.object(control, 'api', return_value={'workflow_runs':[run]}), \
             patch.object(control, 'wait_run', return_value={**run, 'status':'completed','conclusion':'success'}):
            self.assertEqual(control.workflow(self.state, 'build', 'build-windows-release.yml', fields), 123)
        command.assert_not_called()

    def test_successful_record_is_reobserved_without_dispatch(self):
        state = json.loads(self.state.read_text())
        state['stages']['build'] = {'workflow':'build-windows-release.yml','fields':{'version':'1.9.2'},'runId':123}
        control.save(self.state, state)
        with patch.object(control, 'command') as command, patch.object(control, 'wait_run', return_value={'conclusion':'success','run_attempt':2}):
            self.assertEqual(control.workflow(self.state,'build','build-windows-release.yml',{'version':'1.9.2'}),123)
        command.assert_not_called()
        saved=json.loads(self.state.read_text())['stages']['build']
        self.assertEqual(saved['conclusion'],'success')
        self.assertEqual(saved['attempt'],2)

    def test_failed_build_cannot_trigger_an_unproven_rebuild(self):
        state = json.loads(self.state.read_text())
        state['stages']['build'] = {'workflow':'build-windows-release.yml','fields':{},'runId':123}
        control.save(self.state, state)
        with patch.object(control, 'command') as command, patch.object(control, 'wait_run', return_value={'conclusion':'failure','run_attempt':1}), \
             patch.object(control, 'api', return_value={'jobs':[]}):
            with self.assertRaisesRegex(ValueError, 'implicit rebuild'):
                control.workflow(self.state,'build','build-windows-release.yml',{},retry_failed=True)
        command.assert_not_called()

    def test_failed_pypi_recovery_keeps_the_verified_build_and_does_not_rebuild(self):
        fields={'tag':'v1.9.2','build_run_id':'321'}
        state=json.loads(self.state.read_text())
        state['stages']['pypi']={'workflow':'publish-pypi.yml','fields':fields,'runId':123}
        control.save(self.state,state)
        run={'id':123,'head_sha':SHA,'run_attempt':2,'display_title':'Publish v1.9.2 ['+'a'*32+']'}
        with patch.object(control,'command') as command,patch.object(control.uuid,'uuid4') as uuid, \
             patch.object(control,'api',return_value=run), \
             patch.object(control,'wait_run',side_effect=[{'conclusion':'failure','run_attempt':1},dict(run,conclusion='success')]):
            uuid.return_value.hex='a'*32
            self.assertEqual(control.workflow(self.state,'pypi','publish-pypi.yml',fields,retry_failed=True),123)
        self.assertEqual(command.call_args.args[0],['gh','run','rerun','123','--failed','--repo',control.REPO])
        self.assertNotIn('build-windows-release.yml',command.call_args.args[0])

    def test_resume_reuses_standard_tests_only_after_a_receipt_was_uploaded(self):
        fields={'version':'1.9.2','commit':SHA}
        for receipt_passed, uploaded in [(True,True),(False,True),(True,False)]:
            with self.subTest(receipt=receipt_passed,uploaded=uploaded):
                state=json.loads(self.state.read_text())
                state['stages']['build']={'workflow':'build-windows-release.yml','fields':fields,'runId':123}
                control.save(self.state,state)
                jobs={'jobs':[{'name':'build','steps':[
                    {'name':'Build artifacts and verify Windows startup','conclusion':'success'},
                    {'name':'Record passed standard validation','conclusion':'success' if receipt_passed else 'skipped'},
                    {'name':'Upload validation evidence','conclusion':'success' if uploaded else 'failure'}]}]}
                run={'id':124,'head_sha':SHA,'run_attempt':1,'display_title':'Build ['+'a'*32+']'}
                with patch.object(control,'command') as command,patch.object(control.uuid,'uuid4') as uuid, \
                     patch.object(control,'api',side_effect=[jobs,{'workflow_runs':[run]}]), \
                     patch.object(control,'wait_run',side_effect=[{'conclusion':'failure','run_attempt':1},dict(run,conclusion='success')]):
                    uuid.return_value.hex='a'*32
                    control.workflow(self.state,'build','build-windows-release.yml',fields,retry_failed=True)
                args=command.call_args.args[0]
                self.assertIn('reuse_build_run_id=123',args)
                self.assertEqual('validation_run_id=123' in args,receipt_passed and uploaded)

    def test_download_inactivity_kills_only_the_owned_child(self):
        with patch.object(control.subprocess,'Popen') as start, patch.object(control.time,'sleep'), \
             patch.object(control.time,'monotonic',side_effect=[0, 5]):
            process = start.return_value
            process.poll.return_value = None
            with self.assertRaises(TimeoutError):
                control.download(['offline-fixture'], self.root/'download.part', stall=1)
        process.kill.assert_called_once()
        process.wait.assert_called_once()


class EntryPointTests(unittest.TestCase):
    def test_progress_missing_candidate_is_failure_instead_of_skip(self):
        environment = {key:value for key,value in os.environ.items() if not key.startswith('PDF2ZH_TEST_')}
        result = subprocess.run([sys.executable,str(ROOT/'scripts/test_windows_uv_progress.py')],
                                env=environment,capture_output=True,text=True,timeout=30)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Explicit candidate inputs required',result.stderr)

    @unittest.skipUnless(os.name == 'nt', 'PowerShell smoke report contract')
    def test_actual_smoke_success_branch_marks_report_passed(self):
        text = (ROOT/'scripts/test_windows_lifecycle.ps1').read_text(encoding='utf-8')
        body = text.split('if ($SmokeOnly) {',1)[1].split('\n}\n',1)[0]
        # Run the real success branch with only external install/cleanup operations stubbed.
        body = '\n'.join(line for line in body.splitlines() if 'uninstall.ps1' not in line)
        command = "function Wait-PathAbsent {param($Path)}; $WindowsPackage=$null;$AppRoot='fixture';$passed=$false;try {"+body+"} finally {if(-not $passed){exit 3}}"
        result=subprocess.run(['powershell','-NoProfile','-Command',command],capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_no_pypi_still_requires_public_files_before_release(self):
        from test_release_cleanup import BASH
        if not BASH:
            self.skipTest('Bash required')
        text=(ROOT/'scripts/release.sh').read_text(encoding='utf-8')
        gate=text.split('# Skipping upload must never bypass',1)[1].split('\nif [[ "$PUBLISH_RELEASE" -eq 1 ]]; then',1)[0]
        gate=gate.split('\n',1)[1]
        for status in (0,1,2):
            script=f'PUBLISH_RELEASE=1; PUBLISH_PYPI=0; REPLACE_EXISTING=""; pypi_release_complete() {{ return {status}; }}; die() {{ exit 9; }};\n'+gate
            result=subprocess.run([BASH,'--noprofile','--norc'],input=script.encode(),capture_output=True,timeout=30)
            self.assertEqual(result.returncode,0 if status==0 else 9,result.stderr)


if __name__ == '__main__':
    unittest.main()
