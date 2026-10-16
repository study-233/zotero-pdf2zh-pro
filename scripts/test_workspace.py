"""Exercise retention and path boundaries using disposable workspaces only."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

from workspace import MARKER, Workspace, checked, metadata, save


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='workspace 中文 ')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.workspace = Workspace(self.root)

    def old(self, category='report', name='old', days=31, owner=None):
        parent = {'report': self.workspace.reports, 'run': self.workspace.runs, 'build': self.workspace.builds}[category]
        directory = parent / name
        directory.mkdir(parents=True)
        save(directory / MARKER, dict(schema=1, category=category, name=name,
                                      created=time.time() - days * 86400, owner_pid=owner))
        (directory / 'result.txt').write_text('evidence', encoding='utf-8')
        return directory

    def test_preview_does_not_delete_and_apply_expires_only_managed_reports(self):
        old = self.old()
        recent = self.old(name='recent', days=29)
        unknown = self.workspace.reports / 'unregistered'
        unknown.mkdir()
        (unknown / 'user.txt').write_text('keep')
        self.assertEqual(len(self.workspace.clean()), 1)
        self.assertTrue(old.exists())
        self.assertEqual(len(self.workspace.clean(apply=True)), 1)
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())
        self.assertTrue(unknown.exists())

    def test_long_term_markers_and_active_owner_override_retention(self):
        pinned = self.old(name='long-term')
        (pinned / '.keep').touch()
        active = self.old(name='active', owner=os.getpid())
        self.assertEqual(self.workspace.clean(apply=True), [])
        self.assertTrue(active.exists())
        self.assertTrue(pinned.exists())

    def test_keep_on_report_root_protects_all_reports(self):
        old = self.old()
        (self.workspace.reports / '.keep').touch()
        self.assertEqual(self.workspace.clean(apply=True), [])
        self.assertTrue(old.exists())

    def test_old_abandoned_run_expires_but_recent_run_does_not(self):
        abandoned = self.old('run', days=2)
        recent = self.old('run', name='recent', days=0)
        self.workspace.clean(apply=True)
        self.assertFalse(abandoned.exists())
        self.assertTrue(recent.exists())

    def test_abandoned_run_archives_evidence_before_expired_report_cleanup(self):
        run = self.workspace.start('abandoned')
        work, report = Path(run['work']), Path(run['report'])
        (work / 'failure.log').write_text('interrupted')
        for path, category, days in ((work, 'run', 2), (report, 'report', 31)):
            data = metadata(path, category)
            data.update(created=time.time() - days * 86400, owner_pid=None)
            save(path / MARKER, data)
        self.workspace.clean(apply=True)
        self.assertFalse(work.exists())
        self.assertTrue(report.exists())
        with zipfile.ZipFile(report / 'evidence.zip') as archive:
            self.assertEqual(archive.read('failure.log'), b'interrupted')
        self.assertEqual(self.workspace.clean(), [])

    def test_long_term_data_caches_and_backups_are_never_candidates(self):
        protected = ['.local-dev/runtime/config', '.local-dev/runtime/library',
                     '.local-dev/runtime/tasks', '.local-dev/translation-bench',
                     '.tmp/cache', '.tmp/slint-toolchain', '.tmp/slint-target',
                     'server/.venv', 'windows-app/target', 'dist/replacement-backups']
        for name in protected:
            path = self.root / name
            path.mkdir(parents=True, exist_ok=True)
            (path / '.keep-data').write_text('unchanged')
        self.workspace.clean(apply=True, now=time.time() + 1000 * 86400)
        for name in protected:
            self.assertEqual((self.root / name / '.keep-data').read_text(), 'unchanged')

    def test_latest_two_versions_use_numeric_order_and_preserve_pinned_older_build(self):
        for version in ('1.8.0', '1.9.0', '1.10.0', '1.11.0'):
            self.old('build', version)
        (self.workspace.builds / '1.8.0/.keep').touch()
        self.workspace.clean(apply=True)
        self.assertEqual(sorted(path.name for path in self.workspace.builds.iterdir()), ['1.10.0', '1.11.0', '1.8.0'])

    def test_old_named_build_aliases_expire_but_unrelated_files_remain(self):
        directory = self.root / 'dist'
        directory.mkdir()
        for version in ('1.8.0', '1.9.0', '1.10.0'):
            (directory / f'zotero-pdf2zh-pro-{version}-source.zip').write_bytes(b'build')
        (directory / 'unrelated.zip').write_bytes(b'keep')
        self.workspace.clean(apply=True)
        self.assertFalse((directory / 'zotero-pdf2zh-pro-1.8.0-source.zip').exists())
        self.assertTrue((directory / 'unrelated.zip').exists())

    def test_finished_run_keeps_evidence_and_removes_installed_dependencies(self):
        run = self.workspace.start('test')
        work = Path(run['work'])
        (work / 'validation.json').write_text('{"passed":true}')
        (work / 'translation.pdf').write_bytes(b'%PDF-acceptance-output')
        (work / 'runtime').mkdir()
        (work / 'runtime/dependency.json').write_text('{"huge":"dependency"}')
        report = Path(self.workspace.finish(work))
        self.assertFalse(work.exists())
        with zipfile.ZipFile(report / 'evidence.zip') as archive:
            self.assertIn('validation.json', archive.namelist())
            self.assertEqual(archive.read('translation.pdf'), b'%PDF-acceptance-output')
            self.assertNotIn('runtime/dependency.json', archive.namelist())
        self.assertEqual(metadata(report, 'report')['status'], 'passed')

    def test_failure_also_cleans_work_but_retains_failure_evidence(self):
        with self.assertRaisesRegex(RuntimeError, 'expected'):
            with self.workspace.run('failure') as (work, report):
                (work / 'failure.log').write_text('expected failure')
                raise RuntimeError('expected')
        self.assertFalse(work.exists())
        self.assertEqual(metadata(report, 'report')['status'], 'failed')
        with zipfile.ZipFile(report / 'evidence.zip') as archive:
            self.assertEqual(archive.read('failure.log'), b'expected failure')

    def test_evidence_archive_failure_retains_the_original_work(self):
        run = self.workspace.start('failure')
        with patch('workspace.zipfile.ZipFile', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.workspace.finish(Path(run['work']))
        self.assertTrue(Path(run['work']).exists())

    def test_finish_refuses_non_run_paths_even_with_a_marker(self):
        source = self.root / 'source'
        source.mkdir()
        with self.assertRaises(ValueError):
            self.workspace.finish(source)
        self.assertTrue(source.exists())
        with self.assertRaises(ValueError):
            checked(self.workspace.runs / '../../source', self.workspace.runs)

    def test_snapshot_preserves_previous_version_and_never_overwrites_a_pinned_build(self):
        artifact = self.root / 'artifact.zip'
        artifact.write_bytes(b'first')
        first = Path(self.workspace.snapshot('1.0.0', [artifact]))
        artifact.write_bytes(b'second')
        self.workspace.snapshot('1.1.0', [artifact])
        self.assertEqual((first / artifact.name).read_bytes(), b'first')
        (first / '.keep').touch()
        with self.assertRaises(ValueError):
            self.workspace.snapshot('1.0.0', [artifact])
        self.assertEqual((first / artifact.name).read_bytes(), b'first')

    def test_report_creation_prunes_old_reports_automatically(self):
        old = self.old()
        self.workspace.create('new')
        self.assertFalse(old.exists())

    def test_concurrent_removal_of_an_expired_report_is_harmless(self):
        old = self.old()
        def reclaimed_elsewhere(path, parent):
            shutil.rmtree(path)
            raise FileNotFoundError(path)
        with patch('workspace.remove', side_effect=reclaimed_elsewhere):
            self.workspace.clean(apply=True)
        self.assertFalse(old.exists())

    def test_check_detects_scattered_files_and_unknown_managed_entries(self):
        (self.root / '.tmp').mkdir()
        (self.root / '.tmp/experiment.py').write_text('print(1)')
        unknown = self.workspace.reports / 'unknown'
        unknown.mkdir(parents=True)
        self.assertGreaterEqual(len(self.workspace.check()), 2)

    def test_directory_link_removal_never_traverses_its_destination(self):
        old = self.old()
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'sentinel.txt').write_text('keep')
        link = old / 'external'
        if os.name == 'nt':
            result = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(link), str(outside)], capture_output=True)
            if result.returncode:
                self.skipTest('Cannot create a test junction')
        else:
            link.symlink_to(outside, target_is_directory=True)
        self.workspace.clean(apply=True)
        self.assertEqual((outside / 'sentinel.txt').read_text(), 'keep')
        self.assertFalse(old.exists())

    def test_linked_managed_root_is_rejected(self):
        outside = self.root / 'outside'
        outside.mkdir()
        self.workspace.reports.parent.mkdir(parents=True)
        if os.name == 'nt':
            result = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(self.workspace.reports), str(outside)], capture_output=True)
            if result.returncode:
                self.skipTest('Cannot create a test junction')
        else:
            self.workspace.reports.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.workspace.create('blocked')
        self.assertTrue(outside.exists())

    @unittest.skipUnless(shutil.which('pwsh') or shutil.which('powershell'), 'PowerShell required')
    def test_powershell_helper_retains_evidence_and_reclaims_its_run(self):
        source = Path(__file__).parent
        scripts = self.root / 'scripts'
        scripts.mkdir()
        for name in ('workspace.py', 'workspace.ps1'):
            shutil.copy2(source / name, scripts / name)
        check = scripts / 'exercise.ps1'
        check.write_text("""$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'workspace.ps1')
$run=New-WorkspaceRun -Name 'powershell-check'
Set-Content -LiteralPath (Join-Path $run.work 'validation.json') -Value '{"passed":true}'
Complete-WorkspaceRun -Run $run -Passed $true
if (Test-Path -LiteralPath $run.work) {throw 'Temporary environment survived completion.'}
if (-not (Test-Path -LiteralPath (Join-Path $run.report 'evidence.zip'))) {throw 'Evidence was lost.'}
""", encoding='utf-8')
        result = subprocess.run([shutil.which('pwsh') or shutil.which('powershell'), '-NoProfile', '-File', str(check)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node.js required')
    def test_node_helper_creates_managed_report_and_records_process_exit(self):
        scripts = self.root / 'scripts'
        scripts.mkdir()
        for name in ('workspace.py', 'workspace.cjs'):
            shutil.copy2(Path(__file__).with_name(name), scripts / name)
        environment = dict(os.environ, PDF2ZH_WORKSPACE_PYTHON=sys.executable)
        result = subprocess.run(['node', '-e', "const fs=require('node:fs'); const p=require('./scripts/workspace.cjs').newReport('node-check'); fs.writeFileSync(require('node:path').join(p,'result.txt'),'passed');"],
                                cwd=self.root, env=environment, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        reports = list(self.workspace.reports.iterdir())
        self.assertEqual(len(reports), 1)
        self.assertEqual(metadata(reports[0], 'report')['status'], 'passed')
        self.assertIsNone(metadata(reports[0], 'report')['owner_pid'])
        self.assertEqual(self.workspace.check(), [])

    @unittest.skipUnless(shutil.which('node'), 'Node.js required')
    def test_node_failure_archives_evidence_and_reclaims_the_run(self):
        scripts = self.root / 'scripts'
        scripts.mkdir()
        for name in ('workspace.py', 'workspace.cjs'):
            shutil.copy2(Path(__file__).with_name(name), scripts / name)
        environment = dict(os.environ, PDF2ZH_WORKSPACE_PYTHON=sys.executable)
        result = subprocess.run(['node', '-e', "const fs=require('node:fs'); const r=require('./scripts/workspace.cjs').newRun('node-failure'); fs.writeFileSync(require('node:path').join(r.work,'failure.log'),'expected'); process.exitCode=7;"],
                                cwd=self.root, env=environment, capture_output=True, text=True)
        self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
        self.assertEqual(list(self.workspace.runs.iterdir()), [])
        report, = self.workspace.reports.iterdir()
        self.assertEqual(metadata(report, 'report')['status'], 'failed')
        with zipfile.ZipFile(report / 'evidence.zip') as archive:
            self.assertEqual(archive.read('failure.log'), b'expected')


if __name__ == '__main__':
    unittest.main()
