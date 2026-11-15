"""Exercise ownership and upgrade transactions without touching real services or libraries."""
from pathlib import Path
import hashlib
import json
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

    def test_new_submission_during_install_denies_handoff(self):
        self.request.side_effect = HTTPError('local', 409, 'busy', {}, None)
        self.assertEqual(self.run_ensure(), 'deferred')
        self.launch.assert_not_called()
        self.assertEqual((self.root / 'active').read_text(), self.old)

    def test_failed_staging_preserves_online_engine(self):
        self.prepare.side_effect = ValueError('checksum')
        self.assertEqual(self.run_ensure(), 'fallback')
        self.request.assert_not_called()
        self.launch.assert_not_called()

    def test_activation_failure_restarts_last_good_engine(self):
        self.launch.side_effect = [RuntimeError('startup'), 'ready']
        self.health.side_effect = [self.live, None]
        self.assertEqual(self.run_ensure(), 'fallback')
        self.assertEqual([c.args[1] for c in self.launch.call_args_list], [self.new, self.old])

    def test_successful_update_and_restart_retain_previous_revision(self):
        for name in (self.old, self.new, '0' * 64):
            (self.root / 'versions' / name).mkdir()
        self.assertEqual(self.run_ensure(), 'ready')
        self.request.assert_called_once_with(18890, '/internal/managed-replace', 'test-token')
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
        self.assertEqual(self.run_ensure(), 'ready')
        self.assertEqual(self.prepare.call_count, 2)
        self.assertTrue((installed / '.repair').is_file())

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


if __name__ == '__main__':
    unittest.main()
