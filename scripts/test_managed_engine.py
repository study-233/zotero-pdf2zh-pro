"""Exercise ownership and upgrade transactions without touching real services or libraries."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import managed_engine as engine
from build_xpi_engine import build


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bundle = self.root / 'bundle'
        self.bundle.mkdir()
        self.old, self.new = '1' * 64, '2' * 64
        (self.bundle / 'manifest.json').write_text(json.dumps({'id': self.new, 'sha256': '3' * 64}))
        (self.root / 'owner').write_text('owned')
        (self.root / 'active').write_text(self.old)
        data = self.root / 'data'
        data.mkdir()
        self.tasks = data / 'tasks.json'
        self.tasks.write_bytes(b'persisted records')
        (data / '.control-token').write_text('test-token')
        self.live = {'managedOwner': 'owned', 'managedRevision': self.old, 'status': 'ok'}
        self.health = self.mock('health', return_value=self.live)
        self.prepare = self.mock('prepare')
        self.launch = self.mock('launch', return_value='ready')
        self.request = self.mock('request')
        (self.root / 'versions').mkdir()
        self.addCleanup(lambda: self.assertEqual(self.tasks.read_bytes(), b'persisted records'))

    def mock(self, name, **options):
        mock = patch.object(engine, name, **options)
        self.addCleanup(mock.stop)
        return mock.start()

    def run_ensure(self):
        return engine.ensure(self.root, self.bundle, self.root / 'uv', 18890)

    def test_external_service_and_another_profile_are_never_replaced(self):
        for owner in [None, 'someone-else']:
            self.live['managedOwner'] = owner
            self.assertEqual(self.run_ensure(), 'external')
        self.request.assert_not_called()
        self.prepare.assert_not_called()
        self.launch.assert_not_called()

    def test_busy_or_degraded_old_engine_keeps_serving(self):
        for status, busy in [('ok', True), ('degraded', False)]:
            self.live.update(status=status, maintenance={'busy': busy})
            self.assertEqual(self.run_ensure(), 'deferred')
        self.prepare.assert_not_called()
        self.request.assert_not_called()
        self.launch.assert_not_called()

    def test_stopped_update_prepares_and_retains_previous_without_starting(self):
        self.health.return_value = None
        self.assertEqual(engine.ensure(self.root, self.bundle, self.root / 'uv', 18890, True), 'ready')
        self.launch.assert_not_called()
        self.assertEqual((self.root / 'previous').read_text(), self.old)
        self.assertEqual((self.root / 'active').read_text(), self.new)

    def test_relocate_stopped_engine_rebases_paths_and_keeps_original(self):
        import shutil
        profile = self.root / 'profile'
        profile.mkdir()
        source = profile / 'pdf2zh-engine'
        source.mkdir()
        dest = self.root / 'new 位置'
        dest.mkdir()
        folder = engine.version_folder(dest, self.new)
        folder.mkdir(parents=True)
        shutil.copyfile(Path(engine.__file__).resolve().parent.parent / 'server/runtime_storage.py', folder / 'runtime_storage.py')
        (source / 'owner').write_text('source')
        (source / 'active').write_text(self.new)
        data = source / 'data'
        data.mkdir()
        tasks = {'tasks':[{'workspace_dir':str(data / 'paper')}]}
        (data / 'tasks.json').write_text(json.dumps(tasks))
        self.health.return_value = None
        binding = profile / 'pdf2zh-engine-location.json'
        self.assertEqual(engine.relocate(source, dest, self.bundle, self.root / 'uv', 18890, binding), 'ready')
        self.assertEqual(json.loads(binding.read_text())['root'], str(dest))
        self.assertEqual(json.loads((data / 'tasks.json').read_text()), tasks)
        migrated = json.loads((dest / 'data/tasks.json').read_text())
        self.assertEqual(migrated['tasks'][0]['workspace_dir'], str(dest / 'data/paper'))
        self.launch.assert_not_called()

    def test_recently_active_service_defers_update(self):
        self.live['maintenance'] = {'busy': False, 'idleSeconds': 59}
        self.assertEqual(self.run_ensure(), 'deferred')
        self.prepare.assert_not_called()

    def test_new_submission_during_install_denies_handoff(self):
        self.request.side_effect = HTTPError('local', 409, 'busy', {}, None)
        self.assertEqual(self.run_ensure(), 'deferred')
        self.launch.assert_not_called()
        self.assertEqual((self.root / 'active').read_text(), self.old)

    def test_activity_during_staging_defers_even_when_the_old_server_cannot_enforce_idle(self):
        self.health.side_effect = [self.live, {**self.live, 'maintenance': {'idleSeconds': 1}}]
        self.assertEqual(self.run_ensure(), 'deferred')
        self.request.assert_not_called()
        self.launch.assert_not_called()

    def test_failed_staging_preserves_online_engine(self):
        self.prepare.side_effect = ValueError('checksum')
        self.assertEqual(self.run_ensure(), 'fallback')
        self.request.assert_not_called()
        self.launch.assert_not_called()

    def test_activation_failure_restarts_last_good_engine(self):
        self.launch.side_effect = [RuntimeError('startup'), 'ready']
        self.health.side_effect = [self.live, self.live, None]
        self.assertEqual(self.run_ensure(), 'fallback')
        self.assertEqual([c.args[1] for c in self.launch.call_args_list], [self.new, self.old])

    def test_successful_update_and_restart_retain_previous_revision(self):
        for name in (self.old, self.new, '0' * 64):
            (self.root / 'versions' / name).mkdir()
            (self.root / 'versions' / name / '.ready').write_text(name)
        self.assertEqual(self.run_ensure(), 'ready')
        self.request.assert_called_once_with(18890, '/internal/managed-replace', 'test-token', minimum_idle=60)
        self.health.return_value = None
        self.assertEqual(self.run_ensure(), 'ready')
        self.assertEqual((self.root / 'previous').read_text(), self.old)
        self.assertEqual({d.name for d in (self.root/'versions').iterdir()}, {self.old,self.new})

    def test_repeated_recovery_works_after_bad_active_revision(self):
        (self.root / 'active').write_text(self.new)
        (self.root / 'previous').write_text(self.old)
        self.health.return_value = None
        self.launch.side_effect = [RuntimeError('broken'), 'ready']
        self.assertEqual(self.run_ensure(), 'fallback')
        self.assertEqual(self.launch.call_args.args[1], self.old)


    def test_sole_damaged_install_is_repaired_once(self):
        (self.root / 'active').write_text(self.new)
        installed = self.root / 'versions' / self.new
        installed.mkdir()
        (installed / '.ready').write_text(self.new)
        self.health.return_value = None
        self.launch.side_effect = [RuntimeError('damaged environment'), 'ready']
        repairs = []
        self.prepare.side_effect = lambda *args: repairs.append((installed / '.repair').is_file())
        self.assertEqual(self.run_ensure(), 'ready')
        self.assertEqual(self.prepare.call_count, 2)
        self.assertEqual(repairs, [False, True])

    def test_offline_restart_recovers_without_uv(self):
        (self.root / 'previous').write_text(self.new)
        self.health.return_value = None
        self.launch.side_effect = [FileNotFoundError('missing environment'), 'ready']
        self.assertEqual(engine.restart(self.root, 18890), 'fallback')
        self.assertEqual(self.launch.call_args.args[1], self.new)
        self.prepare.assert_not_called()


class BundleTests(unittest.TestCase):
    def test_build_is_complete_and_reproducible(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            build(folder)
            manifest = json.loads((folder/'manifest.json').read_bytes())
            self.assertEqual(hashlib.sha256((folder/'service.zip').read_bytes()).hexdigest(), manifest['sha256'])
            self.assertEqual(set(manifest['platforms']), {'windows-x64','macos-arm64'})
            build(folder)
            self.assertEqual(json.loads((folder/'manifest.json').read_bytes()), manifest)

    def test_damaged_payload_never_runs_the_installer(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(engine.subprocess,'run') as run:
            root = Path(temp)
            revision = '2' * 64
            (root/'manifest.json').write_text(json.dumps({'id':revision,'sha256':'0'*64}))
            (root/'service.zip').write_bytes(b'corrupt')
            with self.assertRaisesRegex(ValueError,'checksum'):
                engine.prepare(root,root,revision,root/'uv')
            run.assert_not_called()
            self.assertFalse((root/'versions').exists())


@unittest.skipUnless(os.name == 'nt', 'Windows native process and DLL paths')
class WindowsRuntimeTests(unittest.TestCase):
    def test_new_revisions_are_compact_and_ready_legacy_installs_keep_their_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = 'a' * 64
            compact = engine.version_folder(root, target)
            self.assertEqual(compact, root / 'versions' / target[:16])
            legacy = root / 'versions' / target
            legacy.mkdir(parents=True)
            self.assertEqual(engine.version_folder(root, target), compact)
            (legacy / '.ready').write_text(target)
            self.assertEqual(engine.version_folder(root, target), legacy)

    def test_service_launch_can_leave_the_installer_job(self):
        root = Path('C:/engine')
        owner = 'test-owner'
        target = 'a' * 64
        live = {'managedOwner': owner, 'managedRevision': target}
        with patch.object(engine.Path, 'open'), patch.object(engine.subprocess, 'Popen') as spawn, patch.object(
                engine, 'health', return_value=live):
            spawn.return_value.poll.return_value = None
            self.assertEqual(engine.launch(root, target, 18890, owner), 'ready')
        flags = spawn.call_args.kwargs['creationflags']
        self.assertTrue(flags & subprocess.CREATE_BREAKAWAY_FROM_JOB)
        self.assertTrue(flags & subprocess.CREATE_NO_WINDOW)

    def test_compact_directory_checks_the_full_revision_before_reuse(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old = 'a' * 64
            new = 'a' * 16 + 'b' * 48
            folder = engine.version_folder(root, old)
            python = engine.interpreter(folder)
            python.parent.mkdir(parents=True)
            python.touch()
            (folder / '.ready').write_text(old)
            with self.assertRaisesRegex(ValueError, 'collision'):
                engine.prepare(root, root / 'bundle', new, root / 'uv.exe')
            self.assertEqual((folder / '.ready').read_text(), old)


if __name__ == '__main__':
    unittest.main()
