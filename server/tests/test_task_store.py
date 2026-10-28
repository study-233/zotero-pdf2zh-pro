"""A damaged task store must never become a new empty writable queue."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from task_manager import TaskManager, TaskRecord, TaskStoreError


class TaskStoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "tasks.json"

    def record(self, name="original"):
        return TaskManager._record_to_persistence(TaskRecord(
            task_id=name, file_name="paper.pdf", service="openai", output_modes=["dual"],
            request_payload={}, workspace_dir=self.root / name, status="failed"))

    def admit(self, manager):
        return manager.create_task(task_id="new", file_name="new.pdf", service="openai",
            output_modes=["dual"], request_payload={}, workspace_dir=self.root / "new")

    def test_invalid_json_and_schema_keep_original_and_reject_every_mutation(self):
        invalid = [b'{"tasks": [broken', b'\xff', b'[]', b'{}',
                   b'{"tasks": {}, "submissions": {}}', b'{"tasks": [], "schemaVersion": 99}',
                   b'{"tasks": [], "submissions": {"id": {}}}',
                   b'{"tasks": [], "unknown": NaN}',
                   json.dumps({"tasks": [self.record(), {"task_id": "partial"}]}).encode(),
                   json.dumps({"tasks": [self.record(), self.record()]}).encode(),
                   json.dumps({"tasks": [{**self.record(), "timing": {"queueSeconds": "bad"}}]}).encode(),
                   json.dumps({"tasks": [{**self.record(), "result_files": {"dual": {"filename": "lost.pdf"}}}]}).encode()]
        for raw in invalid:
            with self.subTest(raw=raw):
                self.path.write_bytes(raw)
                manager = TaskManager(self.path)
                self.assertEqual(manager.task_store_health()["status"], "blocked")
                self.assertEqual(manager._tasks, {}, "never partially load a damaged snapshot")
                for operation in (lambda: self.admit(manager), lambda: manager.cancel_task("original"),
                                  lambda: manager.delete_task("original"), manager.clear_failed_tasks,
                                  lambda: manager.retry_task("original"), lambda: manager.resume_task("original"),
                                  manager.list_tasks_snapshot):
                    with self.assertRaises(TaskStoreError):
                        operation()
                manager._save_persistent_tasks()
                manager.close()
                self.assertEqual(self.path.read_bytes(), raw)
                self.assertTrue(any(p.read_bytes() == raw for p in self.root.glob("tasks.json.recovery-*")))
                self.assertTrue(manager._pending.empty())
                self.assertIsNone(manager._worker)

    def test_valid_backup_is_reported_but_never_replayed_automatically(self):
        previous = json.dumps({"tasks": [self.record()]}).encode()
        self.path.with_suffix(".json.bak").write_bytes(previous)
        self.path.write_bytes(b"broken")
        manager = TaskManager(self.path)
        self.assertTrue(manager.task_store_health()["backupAvailable"])
        self.assertEqual(manager._tasks, {})
        self.assertEqual(self.path.read_bytes(), b"broken")
        # Explicit repair while stopped is picked up by a fresh process.
        self.path.write_bytes(previous)
        restored = TaskManager(self.path)
        self.assertEqual(restored.task_store_health()["status"], "ok")
        self.assertIsNotNone(restored.get_task("original"))
        self.assertIsNone(restored._worker)

    def test_missing_primary_with_backup_is_not_treated_as_first_launch(self):
        self.path.with_suffix(".json.bak").write_text('{"tasks": []}')
        manager = TaskManager(self.path)
        self.assertEqual(manager.task_store_health()["reason"], "missing")
        with self.assertRaises(TaskStoreError):
            self.admit(manager)
        self.assertFalse(self.path.exists())

    def test_read_failure_or_failed_recovery_copy_cannot_enable_writes(self):
        self.path.write_bytes(b"invalid")
        with patch.object(Path, "open", side_effect=PermissionError("denied")):
            manager = TaskManager(self.path)
        self.assertEqual(manager.task_store_health()["reason"], "unreadable")
        with self.assertRaises(TaskStoreError):
            self.admit(manager)
        original_open = Path.open
        def open_without_recovery(path, *args, **kwargs):
            if ".recovery-" in path.name:
                raise OSError("disk full")
            return original_open(path, *args, **kwargs)
        with patch.object(Path, "open", open_without_recovery):
            manager = TaskManager(self.path)
        self.assertEqual(manager.task_store_health()["status"], "blocked")
        self.assertEqual(self.path.read_bytes(), b"invalid")

    def test_successful_saves_keep_previous_snapshot_and_submission_tombstones(self):
        manager = TaskManager(self.path)
        manager._tasks["original"] = TaskManager._record_from_persistence(self.record())
        manager._submissions = {"deleted-request": {"taskId": "deleted", "fingerprint": "hash"}}
        manager._save_persistent_tasks_locked(strict=True)
        first = self.path.read_bytes()
        manager._tasks["second"] = TaskManager._record_from_persistence(self.record("second"))
        manager._save_persistent_tasks_locked(strict=True)
        self.assertEqual(self.path.with_suffix(".json.bak").read_bytes(), first)
        restored = TaskManager(self.path)
        self.assertEqual(len(restored._tasks), 2)
        self.assertEqual(restored._submissions, manager._submissions)

    def test_external_damage_after_startup_is_preserved_and_admission_rolls_back(self):
        self.path.write_text(json.dumps({"tasks": [self.record()]}))
        manager = TaskManager(self.path)
        self.path.write_bytes(b"external damage")
        with self.assertRaises(TaskStoreError):
            self.admit(manager)
        self.assertEqual(set(manager._tasks), {"original"})
        self.assertEqual(self.path.read_bytes(), b"external damage")
        self.assertTrue(manager._pending.empty())
        self.assertIsNone(manager._worker)

    def test_backup_write_failure_leaves_primary_and_new_admission_untouched(self):
        raw = json.dumps({"tasks": [self.record()]}).encode()
        self.path.write_bytes(raw)
        manager = TaskManager(self.path)
        with patch.object(Path, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.admit(manager)
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(set(manager._tasks), {"original"})
        self.assertFalse(list(self.root.glob("*.tmp")))
        self.assertTrue(manager._pending.empty())

    def operation_manager(self, status='failed'):
        from pdf2zh_pro.pdf import TranslationOutputFile
        manager = TaskManager(self.path)
        work = self.root / 'operation'
        output = work / 'output'
        output.mkdir(parents=True, exist_ok=True)
        source = work / 'paper.pdf'
        source.write_bytes(b'%PDF original')
        result = output / 'translated.pdf'
        result.write_bytes(b'%PDF translated')
        record = TaskRecord(task_id='operation', file_name='paper.pdf', service='openai',
            output_modes=['dual'], workspace_dir=work, status=status,
            request_payload={'input_path': str(source), 'output_dir': str(output), 'qps': 8},
            result_files={'dual': TranslationOutputFile('dual', result, result.name)})
        manager._tasks[record.task_id] = record
        manager._save_persistent_tasks_locked(strict=True)
        return manager, record, result

    def test_failed_retry_repair_delete_and_clear_roll_back_without_events_or_file_loss(self):
        from task_manager import TaskPersistenceError
        for operation, status in [('retry_task', 'failed'), ('repair_task', 'completed'),
                                  ('delete_task', 'completed'), ('clear_failed_tasks', 'failed')]:
            for failure_file in ('tasks.json', 'tasks.json.bak'):
                with self.subTest(operation=operation, failure_file=failure_file):
                    manager, record, result = self.operation_manager(status)
                    before = self.path.read_bytes()
                    snapshot = record.to_dict()
                    subscription = manager.subscribe()
                    original = manager._write_snapshot
                    def fail(path, content):
                        if path.name == failure_file:
                            raise OSError('disk full')
                        original(path, content)
                    with patch.object(manager, '_write_snapshot', side_effect=fail), \
                         patch.object(manager, '_start_worker_locked') as worker:
                        with self.assertRaises(TaskPersistenceError):
                            getattr(manager, operation)(*(() if operation == 'clear_failed_tasks' else ('operation',)))
                    self.assertEqual(self.path.read_bytes(), before)
                    self.assertEqual(manager.get_task('operation'), snapshot)
                    self.assertIs(manager._tasks['operation'], record)
                    self.assertTrue(result.is_file())
                    self.assertTrue(subscription.event_queue.empty())
                    self.assertTrue(manager._pending.empty())
                    self.assertTrue(manager.task_store_health()['writeFailed'])
                    worker.assert_not_called()

    def test_successful_mutations_are_durable_before_events_queue_and_file_cleanup(self):
        for operation, status in [('retry_task', 'failed'), ('repair_task', 'completed'),
                                  ('delete_task', 'completed'), ('clear_failed_tasks', 'failed')]:
            with self.subTest(operation=operation):
                manager, record, result = self.operation_manager(status)
                def check_durable(event):
                    rows = {row['task_id']: row for row in json.loads(self.path.read_text())['tasks']}
                    if event['type'] == 'deleted':
                        self.assertNotIn('operation', rows)
                        self.assertTrue(result.exists(), 'commit and event precede file cleanup')
                    else:
                        self.assertEqual(rows['operation']['attempt'], 2)
                        self.assertEqual(rows['operation']['status'], 'queued')
                with patch.object(manager, '_publish_event_locked', side_effect=check_durable), \
                     patch.object(manager, '_start_worker_locked'):
                    getattr(manager, operation)(*(() if operation == 'clear_failed_tasks' else ('operation',)))
                restored = TaskManager(self.path)
                if operation in ('retry_task', 'repair_task'):
                    self.assertEqual(restored.get_task('operation')['attempt'], 2)
                    self.assertTrue(restored.get_task('operation')['canResume'])
                    self.assertTrue(result.exists())
                else:
                    self.assertIsNone(restored.get_task('operation'))
                    self.assertFalse(record.workspace_dir.exists())

    def test_cancel_failure_still_stops_work_and_can_be_saved_by_repeating_cancel(self):
        from task_manager import TaskPersistenceError
        for status in ('queued', 'running'):
            with self.subTest(status=status):
                manager, record, _ = self.operation_manager(status)
                before = self.path.read_bytes()
                callbacks = []
                record.cancel_callback = lambda: callbacks.append(record.cancel_event.is_set())
                with patch.object(manager, '_write_snapshot', side_effect=OSError('disk full')):
                    with self.assertRaises(TaskPersistenceError):
                        manager.cancel_task('operation')
                self.assertEqual(callbacks, [True])
                self.assertTrue(record.cancel_requested)
                self.assertEqual(self.path.read_bytes(), before)
                self.assertTrue(manager.task_store_health()['writeFailed'])
                manager.cancel_task('operation')
                restored = TaskManager(self.path)
                self.assertEqual(restored.get_task('operation')['status'], 'cancelled')
                self.assertFalse(restored.get_task('operation')['canResume'])
                self.assertFalse(manager.task_store_health()['writeFailed'])

    def test_http_operations_report_write_failure_instead_of_success(self):
        import server
        for method, url, status in [('delete', '/tasks/operation', 'completed'),
                ('post', '/tasks/operation/retry', 'failed'), ('post', '/tasks/operation/repair', 'completed'),
                ('post', '/tasks/operation/cancel', 'queued'), ('post', '/tasks/clear-failed', 'failed')]:
            with self.subTest(url=url):
                manager, _, _ = self.operation_manager(status)
                with patch.object(server, 'TASK_MANAGER', manager), \
                     patch.object(manager, '_write_snapshot', side_effect=OSError('disk full')):
                    response = getattr(server.create_app().test_client(), method)(url)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json['code'], 'task_persistence_failed')
                self.assertTrue(response.json['taskStore']['writeFailed'])
