"""Regressions for exact-package validation reuse and change-scoped coverage."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest
from unittest.mock import patch

import release_control as control
import release_policy as policy
import test_release_control as control_tests
from test_release_workflows import inline


class ValidationReceiptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        root_patch = patch.object(control, 'ROOT', self.root)
        root_patch.start(); self.addCleanup(root_patch.stop)
        self.plan = control_tests.PolicyTests().frozen_plan()
        self.plan_file = self.root/'plan.json'
        control.save(self.plan_file, self.plan)
        for name in control.product_paths(self.plan['version']):
            file = self.root/name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(name.encode())
        self.receipt = self.root/'receipt.json'
        with patch.dict(os.environ, GITHUB_RUN_ID='123', GITHUB_RUN_ATTEMPT='2'):
            control.record_standard_validation(self.plan_file, self.receipt)

    def recover(self):
        control.recover_standard_validation(self.plan_file, self.receipt, self.root/'recovered.json')

    def test_same_bytes_and_frozen_plan_reuse_without_rebuilding(self):
        self.recover()
        self.assertEqual(json.loads((self.root/'recovered.json').read_text()),
                         json.loads(self.receipt.read_text()))

    def test_any_other_build_bytes_are_rejected_even_at_the_same_commit(self):
        for name in control.product_paths(self.plan['version']):
            with self.subTest(artifact=name):
                file = self.root/name
                original = file.read_bytes()
                file.write_bytes(b'x'*len(original))  # Same size and source/version, different bytes.
                with self.assertRaisesRegex(ValueError, 'different artifacts'):
                    self.recover()
                file.write_bytes(original)

    def test_changed_baseline_harness_or_lifecycle_cannot_reuse_old_results(self):
        for key, value in [('previousVersion', '1.9.0'), ('harnessSha256', '8'*64),
                           ('lifecycle', 'extended'), ('sourceCommit', 'b'*40), ('cases', [])]:
            with self.subTest(field=key):
                control.save(self.plan_file, dict(self.plan, **{key:value}))
                with self.assertRaisesRegex(ValueError, 'plan or run identity'):
                    self.recover()
        control.save(self.plan_file, self.plan)

    def test_missing_or_unproven_receipt_is_not_a_success(self):
        receipt = json.loads(self.receipt.read_text())
        for change in ({'status':'failed'}, {'runId':''}, {'runAttempt':0}, {'artifacts':{}}):
            with self.subTest(change=change):
                control.save(self.receipt, dict(receipt, **change))
                with self.assertRaises(ValueError):
                    self.recover()
        self.receipt.unlink()
        with self.assertRaises(FileNotFoundError):
            self.recover()

    def test_publication_requires_bound_standard_receipt_and_complete_artifact_set(self):
        manifest = self.root/'checksums.json'
        control.save(manifest, {'version':self.plan['version'], 'commit':control_tests.SHA,
                               'artifacts':control.product_identity(self.plan['version'])})
        control.attach_plan(self.plan_file, manifest, standard_receipt=self.receipt)
        with patch.object(control, 'validate_plan'):
            control.verify_publication_contract(self.root)
            data = json.loads(manifest.read_text())
            data['artifacts'].pop('update.json')
            control.save(manifest, data)
            with self.assertRaisesRegex(ValueError, 'different artifacts'):
                control.verify_publication_contract(self.root)
            (self.root/'standard-validation.json').unlink()
            with self.assertRaisesRegex(ValueError, 'Missing standard'):
                control.verify_publication_contract(self.root)

    def test_recovery_download_rejects_another_run_or_attempt(self):
        code = compile(inline('build-windows-release.yml', 'PY_RECEIPT'), 'PY_RECEIPT', 'exec')
        source = self.root/'download'; source.mkdir()
        receipt = json.loads(self.receipt.read_text())
        control.save(source/'standard-validation.json', receipt)
        run_path = self.root/'run.json'
        for run_id, attempt, accepted in [(123,2,True),(999,2,False),(123,3,False)]:
            control.save(run_path, {'id':run_id,'run_attempt':attempt})
            with patch('sys.argv', ['guard',str(source),str(run_path)]), \
                 patch.dict(os.environ, RUNNER_TEMP=str(self.root), PDF2ZH_VALIDATION_PLAN=str(self.plan_file)):
                if accepted:
                    exec(code, {})
                else:
                    with self.assertRaisesRegex(AssertionError, 'Wrong validation attempt'):
                        exec(code, {})


class ScopedSelectionTests(unittest.TestCase):
    def select(self, *changes, **kwargs):
        return policy.select(list(changes), '1.9.2', '1.9.3', **kwargs)

    def test_ordinary_and_container_only_changes_do_not_run_windows_reinstallation(self):
        for path in ['plugin/src/hooks.ts','server/chatgpt_client.py','server/Dockerfile',
                     'compose.yaml','server/tests/test_glossary_manager.py']:
            with self.subTest(path=path):
                plan = self.select(path)
                self.assertEqual(plan['lifecycle'], 'standard')
                self.assertEqual(len(plan['cases']), 1)
                self.assertFalse(plan['extendedValidation'])

    def test_data_and_dependencies_test_old_data_without_reinstallation(self):
        for path in ['server/glossary_manager.py','server/chatgpt_auth.py','server/task_manager.py',
                     'server/selection_cache.py','server/pyproject.toml','server/uv.lock']:
            with self.subTest(path=path):
                plan = self.select(path)
                self.assertEqual(plan['lifecycle'], 'standard')
                self.assertEqual({c['id'] for c in plan['cases']},
                                 {'recent-upgrade','oldest-upgrade','legacy-upgrade'})
                self.assertFalse(plan['fullValidation'])

    def test_installation_unknown_paths_and_explicit_full_keep_reinstallation_and_migration(self):
        for path in ['scripts/windows/install.ps1','scripts/windows/relocate.ps1',
                     'server/new_data_migration.py','server/babeldoc/new_store.py']:
            with self.subTest(path=path):
                plan = self.select(path)
                self.assertEqual(plan['lifecycle'], 'extended')
                self.assertEqual(len(plan['cases']), 3)
        for flags in ({'full':True}, {'compatibility':True}):
            plan = self.select(**flags)
            self.assertEqual(plan['lifecycle'], 'extended')
            self.assertTrue(plan['fullValidation'])

    def test_combined_risks_union_coverage_and_deduplicate_baselines(self):
        plan = self.select('server/task_manager.py','scripts/windows/install.ps1','server/uv.lock')
        self.assertEqual(plan['lifecycle'], 'extended')
        self.assertEqual(len(plan['cases']), 3)
        for previous in ['1.9.0','1.8.2']:
            plan = policy.select([], previous, '1.9.3', full=True)
            self.assertEqual(sum(c['baseline'] == previous for c in plan['cases']), 1)

    def test_frozen_plan_cannot_remove_lifecycle_checks_even_if_upgrade_cases_are_unchanged(self):
        plan = control_tests.PolicyTests().frozen_plan()
        # An unrecognised plan schema must not silently acquire new selection semantics.
        with self.assertRaisesRegex(ValueError, 'versions'):
            control.validate_plan(dict(plan, schemaVersion=1))
        with patch.object(control, 'command', return_value=('b'*40).encode()), \
             patch.object(control, 'changed_paths', return_value=[]):
            with self.assertRaisesRegex(ValueError, 'coverage'):
                control.validate_plan(dict(plan, lifecycle='extended'))

    def test_server_version_only_changes_do_not_become_data_migrations(self):
        old = 'VERSION = "1.9.2"\nDATA_SCHEMA = 1\n'
        new = old.replace('1.9.2','1.9.3')
        for content, expected in [(new, []), (new.replace('SCHEMA = 1','SCHEMA = 2'), ['server/server.py'])]:
            with patch.object(policy.subprocess,'check_output',side_effect=['server/server.py\n', old, content]):
                self.assertEqual(policy.changed_paths(Path.cwd(),'old','new'), expected)


@unittest.skipUnless(os.name == 'nt', 'Exercise actual Windows workflow steps with isolated stubs')
class WindowsSchedulingTests(unittest.TestCase):
    def body(self, name):
        workflow = (Path(__file__).resolve().parents[1]/'.github/workflows/build-windows-release.yml').read_text(encoding='utf-8')
        step = workflow.split('      - name: '+name+'\n',1)[1].split('\n      - name:',1)[0]
        return textwrap.dedent(step.split('        run: |\n',1)[1])

    def run_step(self, root, script, plan):
        plan_path=root/'plan.json'; control.save(plan_path,plan)
        environment=dict(os.environ, PDF2ZH_VALIDATION_PLAN=str(plan_path), GITHUB_ENV=str(root/'env'),
                         RELEASE_VERSION='1.9.3', RELEASE_COMMIT=control_tests.SHA, RUNNER_TEMP=str(root),
                         PDF2ZH_STANDARD_RECEIPT=str(root/'receipt.json'), VALIDATION_PLAN_INPUT='')
        script="$ErrorActionPreference='Stop'; $PSDefaultParameterValues['Out-File:Encoding']='utf8';\n"+script
        return subprocess.run(['powershell','-NoProfile','-Command',script],cwd=root,env=environment,
                              capture_output=True,text=True,timeout=30)

    def test_deferring_history_does_not_change_the_selected_remote_lifecycle(self):
        for path, expected_smoke in [('server/task_manager.py','True'),('scripts/windows/install.ps1','False')]:
            plan=policy.select([path],'1.9.2','1.9.3')
            for deferred in ['true','false']:
                with self.subTest(path=path,deferred=deferred), tempfile.TemporaryDirectory() as directory:
                    root=Path(directory)
                    prepare=self.body('Prepare frozen validation plan')
                    prepare=prepare.replace('${{ inputs.full_validation }}','false').replace('${{ inputs.defer_extended_validation }}',deferred)
                    result=self.run_step(root,"function uv { $global:LASTEXITCODE=0 };\n"+prepare,plan)
                    self.assertEqual(result.returncode,0,result.stderr)
                    self.assertIn('RUN_UPGRADE_VALIDATION='+str(deferred != 'true').lower(),(root/'env').read_text())
                    (root/'scripts').mkdir()
                    (root/'scripts/test_windows_lifecycle.ps1').write_text(
                        'param($WindowsPackage,$PackageSource,[switch]$SmokeOnly)\n'
                        '$SmokeOnly.ToString() | Set-Content mode.txt\n$global:LASTEXITCODE=0\n',encoding='utf-8')
                    (root/'server/dist').mkdir(parents=True)
                    (root/'server/dist/zotero_pdf2zh_pro-1.9.3-py3-none-any.whl').touch()
                    result=self.run_step(root,self.body('Verify installed startup and upgrade'),plan)
                    self.assertEqual(result.returncode,0,result.stderr)
                    self.assertEqual((root/'mode.txt').read_text().strip(),expected_smoke)

    def test_failed_receipt_verification_never_enables_skipping_tests(self):
        body=self.body('Bind recovered validation to exact packages and plan')
        for status in [0,17]:
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                root=Path(directory)
                result=self.run_step(root,f'function uv {{ $global:LASTEXITCODE={status} }};\n'+body,{})
                self.assertEqual(result.returncode,status,result.stderr)
                self.assertEqual((root/'env').exists(),status==0)
                if status==0:
                    self.assertIn('REUSE_VALIDATION=true',(root/'env').read_text())


class PublicationInputTests(unittest.TestCase):
    def test_pypi_only_and_github_publication_both_require_a_verified_build(self):
        code = compile(inline('publish-pypi.yml','PY_INPUT'), 'PY_INPUT', 'exec')
        for github in ['true','false']:
            with patch.dict(os.environ, PUBLISH_GITHUB=github):
                for value in ['', '0', 'abc', '-1']:
                    with self.subTest(github=github,run=value), patch('sys.argv',['guard',value]):
                        with self.assertRaises(AssertionError):
                            exec(code, {})
                with patch('sys.argv',['guard','123']):
                    exec(code, {})


if __name__ == '__main__':
    unittest.main()
