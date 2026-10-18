"""Task admission, durable recovery and wall-clock-independent elapsed time."""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from task_manager import TaskManager


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.manager = TaskManager(self.root / "tasks.json")
        self.worker = patch.object(self.manager, "_start_worker_locked").start()
        self.addCleanup(patch.stopall)
        self.addCleanup(self.manager.close)

    def create(self, name, request_id=None, *, content=b"pdf", scope="", **options):
        work = self.root / name
        work.mkdir(exist_ok=True)
        source = work / "paper.pdf"
        source.write_bytes(content)
        return self.manager.create_task(task_id=name, file_name="paper.pdf", service="openai",
            output_modes=["dual"], workspace_dir=work, request_id=request_id, dedup_scope=scope,
            request_payload={"input_path": str(source), "output_dir": str(work / "output"),
                             "qps": 7, "pool_size": 9, **options})

    def test_replayed_and_duplicate_submissions_share_one_durable_task(self):
        self.create("first", "request-one")
        self.assertEqual(self.create("replay", "request-one")["taskId"], "first")
        self.assertEqual(self.create("alias", "request-two")["taskId"], "first")
        self.assertEqual(self.manager._pending.qsize(), 1)
        record = self.manager._tasks["first"]
        record.status = "completed"
        record.time_phase(terminal=True)
        self.manager._save_persistent_tasks()
        restored = TaskManager(self.root / "tasks.json")
        self.addCleanup(restored.close)
        original = self.manager
        self.manager = restored
        self.assertEqual(self.create("late", "request-two")["taskId"], "first")
        self.assertIsNone(restored._worker)
        self.manager = original

    def test_changed_inputs_conflict_but_new_intents_and_items_are_allowed(self):
        self.create("first", "request-one")
        for options in ({"content": b"other"}, {"qps": 8}, {"llm_api": {"model": "other"}}):
            with self.assertRaisesRegex(ValueError, "不同文件或配置"):
                self.create("mismatch", "request-one", **options)
        self.assertEqual(self.create("other-item", "request-other", scope="item-2")["taskId"], "other-item")
        self.manager._tasks["first"].status = "completed"
        self.assertEqual(self.create("again", "request-new")["taskId"], "again")
        self.assertEqual(self.create("legacy")["taskId"], "legacy")

    def test_simultaneous_submissions_are_admitted_once(self):
        barrier = threading.Barrier(3)
        results, errors = [], []
        def submit(index):
            try:
                barrier.wait()
                results.append(self.create(str(index), f"request-{index}"))
            except Exception as error:
                errors.append(error)
        threads = [threading.Thread(target=submit, args=(index,)) for index in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(3)
            self.assertFalse(thread.is_alive())
        self.assertFalse(errors)
        self.assertEqual(len({row["taskId"] for row in results}), 1)
        self.assertEqual(self.manager._pending.qsize(), 1)
        saved = json.loads((self.root / "tasks.json").read_text())
        self.assertEqual(len(saved["tasks"]), 1)
        self.assertEqual(len(saved["submissions"]), 2)

    def test_failed_persistence_never_acknowledges_or_executes_submission(self):
        with patch.object(Path, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.create("first", "request-one")
        self.assertFalse(self.manager._tasks)
        self.assertFalse(self.manager._submissions)
        self.assertTrue(self.manager._pending.empty())
        self.worker.assert_not_called()
        self.assertEqual(self.create("retry", "request-one")["taskId"], "retry")

    def test_deleted_task_keeps_a_submission_tombstone(self):
        self.create("first", "request-one")
        self.manager.cancel_task("first")
        self.manager.delete_task("first")
        with self.assertRaisesRegex(ValueError, "已删除"):
            self.create("late", "request-one")

    def test_recovery_preserves_options_and_cancellation_intent(self):
        self.create("first", "request-one")
        self.create("second", "request-two", content=b"second")
        self.create("cancelled", "request-three", content=b"cancelled")
        first = self.manager._tasks["first"]
        first.status = "running"
        first.time_phase("executionSeconds")
        first.progress_detail["cancelReason"] = "shutdown"
        self.manager.cancel_task("cancelled")
        self.manager.close()
        # Simulate the running worker finishing its cooperative shutdown.
        self.manager._finish_cancellation_locked(first)
        self.manager._save_persistent_tasks()
        restored = TaskManager(self.root / "tasks.json")
        self.addCleanup(restored.close)
        self.assertEqual(restored.get_task("first")["recoveryState"], "interrupted")
        self.assertEqual(restored.get_task("second")["recoveryState"], "queued")
        self.assertFalse(restored.get_task("cancelled")["canResume"])
        before = dict(restored._tasks["first"].request_payload)
        with patch.object(restored, "_start_worker_locked"):
            restored.resume_task("first")
            restored.resume_task("first")
        self.assertEqual(restored._pending.qsize(), 1)
        self.assertEqual(restored._tasks["first"].request_payload, before)
        self.assertEqual(restored.get_task("first")["attempt"], 1)
        restored.cancel_task("second")
        self.assertFalse(restored.get_task("second")["canResume"])
        self.assertEqual(restored.get_task("second")["status"], "cancelled")

    def test_resume_does_not_start_when_cleanup_failed_or_source_missing(self):
        self.create("first", "request-one")
        self.manager.close()
        restored = TaskManager(self.root / "tasks.json")
        self.addCleanup(restored.close)
        restored._queue_blocked = True
        with self.assertRaisesRegex(ValueError, "CANCEL_CLEANUP_FAILED"):
            restored.resume_task("first")
        restored._queue_blocked = False
        Path(restored._tasks["first"].request_payload["input_path"]).unlink()
        with self.assertRaisesRegex(ValueError, "no longer available"):
            restored.resume_task("first")
        self.assertTrue(restored.get_task("first")["canResume"])
        self.assertIsNone(restored._worker)

    def test_failed_resume_persistence_keeps_recovery_available(self):
        self.create("first", "request-one")
        self.manager.close()
        restored = TaskManager(self.root / "tasks.json")
        self.addCleanup(restored.close)
        before = restored.get_task("first")
        with patch.object(Path, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                restored.resume_task("first")
        self.assertEqual(restored.get_task("first"), before)
        self.assertTrue(restored._pending.empty())
        self.assertIsNone(restored._worker)

    def test_resumed_worker_reuses_the_workspace_and_runs_only_once(self):
        self.create("first", "request-one")
        checkpoint = self.root / "first" / "checkpoint-fixture"
        checkpoint.write_bytes(b"saved paragraph progress")
        self.manager.close()
        calls = []
        async def translate(payload, task_id, **kwargs):
            calls.append(task_id)
            self.assertEqual(payload["qps"], 7)
            self.assertEqual(payload["pool_size"], 9)
            self.assertEqual(Path(payload["input_path"]).parent, checkpoint.parent)
            self.assertEqual(checkpoint.read_bytes(), b"saved paragraph progress")
            return SimpleNamespace(files={}, translation_summary=None, failed_paragraphs=[])
        restored = TaskManager(self.root / "tasks.json", translator=translate)
        self.addCleanup(restored.close)
        restored.resume_task("first")
        restored.resume_task("first")
        deadline = time.monotonic() + 3
        while restored.get_task("first")["status"] in {"queued", "running"} and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(restored.get_task("first")["status"], "completed")
        self.assertEqual(calls, ["first"])
        self.assertTrue(restored.get_task("first")["timing"]["complete"])

    def test_legacy_task_time_is_unknown_until_resumed_and_remains_partial(self):
        self.create("first", "request-one")
        self.manager.close()
        path = self.root / "tasks.json"
        saved = json.loads(path.read_text())
        saved.pop("submissions")
        record = saved["tasks"][0]
        record.pop("timing")
        record.pop("recovery_state")
        record["status"] = "running"
        record["metrics"]["stageDurations"] = {"Queue Wait": .25}
        path.write_text(json.dumps(saved))
        restored = TaskManager(path)
        self.addCleanup(restored.close)
        self.assertIsNone(restored.get_task("first")["timing"])
        self.assertEqual(restored.get_task("first")["metrics"]["stageDurations"], {"Engine Wait": .25})
        with patch.object(restored, "_start_worker_locked"):
            resumed = restored.resume_task("first")
        self.assertFalse(resumed["timing"]["complete"])

    def test_queue_and_execution_are_separate_and_terminal_time_is_frozen(self):
        clock = [100.0]
        with patch("task_manager.time.monotonic", side_effect=lambda: clock[0]):
            self.create("first", "request-one")
            clock[0] = 105
            self.assertEqual(self.manager.get_task("first")["timing"]["queueSeconds"], 5)
            async def translate(*args, **kwargs):
                clock[0] = 108
                kwargs["metrics_callback"]({"stageDurations": {"Engine Wait": 0.1}})
                return SimpleNamespace(files={}, translation_summary=None, failed_paragraphs=[])
            self.manager._translator = translate
            self.manager._run_task("first", 1)
            clock[0] = 10000
            task = self.manager.get_task("first")
            self.assertEqual(task["timing"]["queueSeconds"], 5)
            self.assertEqual(task["timing"]["executionSeconds"], 3)
            self.assertEqual(task["timing"]["totalSeconds"], 8)
            self.assertTrue(task["timing"]["complete"])
            self.assertEqual(task["metrics"]["stageDurations"]["Queue Wait"], 5)
            self.assertEqual(task["metrics"]["stageDurations"]["Engine Wait"], .1)
            self.manager.repair_task("first")
            self.assertEqual(self.manager.get_task("first")["timing"]["totalSeconds"], 0)

    def test_abrupt_restart_reports_partial_time_and_excludes_downtime(self):
        clock = [100.0]
        with patch("task_manager.time.monotonic", side_effect=lambda: clock[0]):
            self.create("first", "request-one")
            record = self.manager._tasks["first"]
            clock[0] = 105
            record.status = "running"
            record.time_phase("executionSeconds")
            clock[0] = 112
            self.manager._save_persistent_tasks()
            clock[0] = 1000
            restored = TaskManager(self.root / "tasks.json")
            self.addCleanup(restored.close)
            task = restored.get_task("first")
            self.assertFalse(task["timing"]["complete"])
            self.assertEqual(task["timing"]["totalSeconds"], 12)
            with patch.object(restored, "_start_worker_locked"):
                restored.resume_task("first")
            clock[0] = 1003
            self.assertEqual(restored.get_task("first")["timing"]["queueSeconds"], 8)
            self.assertEqual(restored.get_task("first")["timing"]["executionSeconds"], 7)

    def test_cancel_survives_abrupt_restart_without_becoming_resumable(self):
        self.create("first", "request-one")
        record = self.manager._tasks["first"]
        record.status = "running"
        self.manager.cancel_task("first")
        restored = TaskManager(self.root / "tasks.json")
        self.addCleanup(restored.close)
        task = restored.get_task("first")
        self.assertEqual(task["status"], "cancelled")
        self.assertFalse(task["canResume"])
        self.assertTrue(task["cancelRequested"])


if __name__ == "__main__":
    unittest.main()
