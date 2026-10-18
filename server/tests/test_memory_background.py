import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from task_manager import TaskManager, TaskRecord


class BackgroundMemoryTests(unittest.TestCase):
    def test_startup_is_nonblocking_and_failed_sync_retries_without_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'paper.pdf'
            source.write_bytes(b'%PDF-1.4\n')
            manager = TaskManager(root / 'tasks.json')
            manager._tasks['one'] = TaskRecord('one', 'paper.pdf', 'openai', ['dual'],
                {'input_path': str(source)}, root, status='completed',
                progress_detail={'translationMemorySync': 'pending'})
            manager._save_persistent_tasks()
            manager.close()
            entered, release, synced = (threading.Event() for _ in range(3))
            calls = []
            def sync(path, payload, **options):
                calls.append(options)
                if len(calls) == 1:
                    entered.set()
                    release.wait(3)
                    raise OSError('database locked')
                synced.set()
                return 1
            with patch('translation_memory.sync_recovery', side_effect=sync), \
                 patch('task_manager.MEMORY_RETRY_SECONDS', (0.02,)), \
                 patch('task_manager.run_translation') as provider:
                started = time.monotonic()
                restored = TaskManager(root / 'tasks.json')
                try:
                    self.assertLess(time.monotonic() - started, .5)
                    self.assertTrue(entered.wait(1))
                    self.assertEqual(restored.get_task('one')['status'], 'completed')
                    release.set()
                    self.assertTrue(synced.wait(2))
                    deadline = time.monotonic() + 2
                    while restored.get_task('one')['translationMemorySync'] != 'synced' and time.monotonic() < deadline:
                        time.sleep(.01)
                    self.assertEqual(restored.get_task('one')['translationMemorySync'], 'synced')
                    self.assertEqual(len(calls), 2)
                    self.assertEqual(calls[0]['timeout'], 1)
                    provider.assert_not_called()
                finally:
                    release.set()
                    restored.close()
            data = json.loads((root / 'tasks.json').read_text())
            self.assertEqual(data['tasks'][0]['progress_detail']['translationMemorySync'], 'synced')

    def test_deleted_or_new_attempt_cannot_be_marked_synced_by_an_old_sync(self):
        for delete in (False, True):
            with self.subTest(delete=delete), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                entered, release = threading.Event(), threading.Event()
                def sync(*args, **options):
                    entered.set()
                    release.wait(2)
                with patch('translation_memory.sync_recovery', side_effect=sync):
                    manager = TaskManager(root / 'tasks.json')
                    manager._tasks['one'] = TaskRecord('one', 'paper.pdf', 'openai', ['dual'], {}, root / 'one',
                        status='completed', progress_detail={'translationMemorySync': 'pending'})
                    manager._schedule_memory_sync('one')
                    self.assertTrue(entered.wait(1))
                    with manager._lock:
                        if delete:
                            del manager._tasks['one']
                        else:
                            manager._tasks['one'].attempt += 1
                            manager._tasks['one'].status = 'queued'
                    release.set()
                    if manager._memory_worker:
                        manager._memory_worker.join(3)
                    if not delete:
                        self.assertEqual(manager.get_task('one')['translationMemorySync'], 'pending')
                    manager.close()

    def test_manual_retry_persists_pending_and_rejects_tasks_without_a_result(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = TaskManager(root / 'tasks.json')
            self.addCleanup(manager.close)
            manager._tasks['one'] = TaskRecord('one', 'paper.pdf', 'openai', ['dual'], {}, root,
                status='completed', progress_detail={'translationMemorySync': 'synced'})
            with patch.object(manager, '_schedule_memory_sync') as queue:
                self.assertEqual(manager.retry_memory_sync('one')['translationMemorySync'], 'pending')
                queue.assert_called_once_with('one')
            with self.assertRaises(ValueError):
                manager.delete_task('one')
            self.assertEqual(json.loads((root / 'tasks.json').read_text())['tasks'][0]['progress_detail']['translationMemorySync'], 'pending')
            manager._tasks['one'].status = 'failed'
            with self.assertRaises(ValueError):
                manager.retry_memory_sync('one')
            self.assertIsNone(manager.retry_memory_sync('missing'))
