from __future__ import annotations

import asyncio
import queue
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from task_manager import TaskManager, TaskRecord


class TaskSyncTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.manager = TaskManager()
        self.addCleanup(self.manager.close, timeout=2)

    def translator(self, callback):
        self.enterContext(patch("task_manager.translate_pdf_with_callbacks", side_effect=callback))
        # Stop the worker before restoring the real translator, including on failure.
        self.addCleanup(self.manager.close, timeout=2)

    def create(self, task_id):
        workspace = self.root / task_id
        workspace.mkdir()
        source = workspace / "paper.pdf"
        source.write_bytes(b"pdf")
        return self.manager.create_task(
            task_id=task_id, file_name="paper.pdf", service="openai",
            output_modes=["dual"], workspace_dir=workspace,
            request_payload={"input_path": str(source), "output_dir": str(workspace / "output")},
        )

    @staticmethod
    def result():
        return SimpleNamespace(files={}, translation_summary=None, failed_paragraphs=[])

    def await_status(self, task_id, expected):
        subscription = self.manager.subscribe()
        try:
            current = self.manager.get_task(task_id)
            if current["status"] == expected:
                return current
            deadline = time.monotonic() + 2
            while True:
                try:
                    event = self.manager.next_subscription_event(
                        subscription, timeout=max(deadline - time.monotonic(), 0)
                    )
                except queue.Empty:
                    self.fail(f"Task {task_id} did not reach {expected}")
                task = event.get("task", {})
                if task.get("taskId") == task_id and task.get("status") == expected:
                    return task
        finally:
            self.manager.unsubscribe(subscription)

    def test_one_lazy_worker_runs_tasks_in_fifo_order(self):
        entered = threading.Event()
        release = threading.Event()
        calls = []

        async def translate(payload, task_id, **kwargs):
            calls.append(task_id)
            if task_id == "first":
                entered.set()
                self.assertTrue(release.wait(2))
            return self.result()

        self.assertIsNone(self.manager._worker)
        self.translator(translate)
        self.addCleanup(release.set)
        self.create("first")
        self.assertTrue(entered.wait(2))
        worker = self.manager._worker
        second = self.create("second")
        third = self.create("third")
        self.assertEqual(second["status"], "queued")
        self.assertEqual(third["status"], "queued")
        self.assertEqual(calls, ["first"])
        self.assertIs(self.manager._worker, worker)
        release.set()
        self.await_status("third", "completed")
        self.assertEqual(calls, ["first", "second", "third"])

    def test_cancelled_queue_entry_cannot_execute_a_later_repair_attempt(self):
        entered = threading.Event()
        release = threading.Event()
        calls = []

        async def translate(payload, task_id, **kwargs):
            calls.append((task_id, self.manager.get_task(task_id)["attempt"]))
            if task_id == "first":
                entered.set()
                self.assertTrue(release.wait(2))
            return self.result()

        self.translator(translate)
        self.addCleanup(release.set)
        self.create("first")
        self.assertTrue(entered.wait(2))
        self.create("cancelled")
        old_event = self.manager._tasks["cancelled"].cancel_event
        self.assertEqual(self.manager.cancel_task("cancelled")["status"], "cancelled")
        self.assertTrue(old_event.is_set())
        self.create("third")
        repaired = self.manager.repair_task("cancelled")
        self.assertEqual(repaired["attempt"], 2)
        self.assertFalse(self.manager._tasks["cancelled"].cancel_event.is_set())
        release.set()
        self.await_status("cancelled", "completed")
        self.assertEqual(calls, [("first", 1), ("third", 1), ("cancelled", 2)])

    def test_running_cancel_wins_result_commit_and_prevents_early_repair(self):
        entered = threading.Event()
        release = threading.Event()
        observed = []

        async def translate(payload, task_id, *, cancel_event, **kwargs):
            observed.append(cancel_event)
            entered.set()
            self.assertTrue(release.wait(2))
            return self.result()

        self.translator(translate)
        self.addCleanup(release.set)
        self.create("first")
        self.assertTrue(entered.wait(2))
        cancelled = self.manager.cancel_task("first")
        self.assertEqual(cancelled["status"], "cancelling")
        self.assertTrue(observed[0].is_set())
        with self.assertRaisesRegex(ValueError, "Active task"):
            self.manager.repair_task("first")
        release.set()
        terminal = self.await_status("first", "cancelled")
        self.assertGreater(terminal["revision"], cancelled["revision"])
        self.assertEqual(terminal["resultFiles"], {})

    def test_worker_continues_after_cancelled_error_and_failure_can_retry(self):
        calls = []

        async def translate(payload, task_id, **kwargs):
            calls.append(task_id)
            if len(calls) == 1:
                raise asyncio.CancelledError()
            if len(calls) == 2:
                raise RuntimeError("temporary failure")
            return self.result()

        self.translator(translate)
        self.create("cancelled")
        self.await_status("cancelled", "cancelled")
        self.create("retry")
        self.await_status("retry", "failed")
        snapshot = self.manager.retry_task("retry")
        self.assertEqual(snapshot["attempt"], 2)
        self.await_status("retry", "completed")
        self.assertEqual(calls, ["cancelled", "retry", "retry"])

    def test_close_and_reload_preserve_interrupted_work_without_starting_queued_tasks(self):
        entered = threading.Event()
        calls = []
        self.manager._persistence_path = self.root / "tasks.json"

        async def translate(payload, task_id, *, cancel_event, **kwargs):
            calls.append(task_id)
            entered.set()
            self.assertTrue(cancel_event.wait(2))
            raise asyncio.CancelledError()

        self.translator(translate)
        self.create("first")
        self.assertTrue(entered.wait(2))
        self.create("second")
        self.create("user-cancelled")
        self.manager.cancel_task("user-cancelled")
        self.manager.close(timeout=2)
        self.assertFalse(self.manager._worker.is_alive())
        self.assertEqual(calls, ["first"])
        restored = TaskManager(self.manager._persistence_path)
        self.assertIsNone(restored._worker)
        self.assertEqual(
            {task["taskId"]: task["status"] for task in restored.list_tasks()},
            {"first": "incomplete", "second": "incomplete", "user-cancelled": "cancelled"},
        )
        for task_id in ("first", "second"):
            task = restored.get_task(task_id)
            self.assertFalse(task["cancelRequested"])
            self.assertTrue(task["canRepair"])
        self.assertTrue(restored.get_task("user-cancelled")["cancelRequested"])
        with self.assertRaisesRegex(ValueError, "shutting down"):
            self.manager.repair_task("first")

    def test_shutdown_wins_result_commit_as_incomplete(self):
        entered = threading.Event()

        async def translate(payload, task_id, *, cancel_event, **kwargs):
            entered.set()
            self.assertTrue(cancel_event.wait(2))
            return self.result()

        self.translator(translate)
        self.create("first")
        self.assertTrue(entered.wait(2))
        self.manager.close(timeout=2)
        task = self.manager.get_task("first")
        self.assertEqual(task["status"], "incomplete")
        self.assertFalse(task["cancelRequested"])
        self.assertEqual(task["resultFiles"], {})

    def test_shutdown_preserves_an_explicit_running_cancellation(self):
        entered = threading.Event()
        release = threading.Event()

        async def translate(payload, task_id, **kwargs):
            entered.set()
            self.assertTrue(release.wait(2))
            return self.result()

        self.translator(translate)
        self.addCleanup(release.set)
        self.create("first")
        self.assertTrue(entered.wait(2))
        self.assertEqual(self.manager.cancel_task("first")["status"], "cancelling")
        with self.assertRaisesRegex(RuntimeError, "shutdown timeout"):
            self.manager.close(timeout=0)
        release.set()
        self.manager.close(timeout=2)
        task = self.manager.get_task("first")
        self.assertEqual(task["status"], "cancelled")
        self.assertTrue(task["cancelRequested"])

    def test_snapshot_is_complete_immutable_and_changes_have_ordered_revisions(self):
        with patch.object(self.manager, "_start_worker_locked"):
            for index in range(130):
                self.create(f"task-{index}")
        subscription = self.manager.subscribe()
        self.addCleanup(self.manager.unsubscribe, subscription)
        initial = subscription.initial_events
        full = self.manager.list_tasks_snapshot()
        self.assertEqual(len(initial), 130)
        self.assertTrue(subscription.event_queue.empty())
        self.assertEqual({event["task"]["taskId"] for event in initial}, {task["taskId"] for task in full["tasks"]})
        for event in initial:
            self.assertEqual(event["revision"], event["task"]["revision"])
            self.assertEqual(event["serverInstanceId"], full["serverInstanceId"])
            self.assertLessEqual(event["revision"], full["revision"])
        before = next(event["task"] for event in initial if event["task"]["taskId"] == "task-0")
        self.manager._handle_metrics_event("task-0", {"requests": {"active": 2}})
        self.manager.cancel_task("task-0")
        deleted = self.manager.delete_task("task-0")
        events = [self.manager.next_subscription_event(subscription, timeout=0) for _ in range(3)]
        self.assertEqual([event["type"] for event in events], ["task", "task", "deleted"])
        self.assertEqual([event["revision"] for event in events], list(range(full["revision"] + 1, full["revision"] + 4)))
        self.assertEqual(deleted["revision"], events[-1]["revision"])
        self.assertEqual(before["status"], "queued")
        self.assertEqual(before["metrics"]["requests"]["active"], 0)
        self.assertNotIn("task-0", {task["taskId"] for task in self.manager.list_tasks_snapshot()["tasks"]})

    def test_concurrent_metrics_updates_publish_in_revision_order(self):
        with patch.object(self.manager, "_start_worker_locked"):
            self.create("first")
        subscription = self.manager.subscribe()
        self.addCleanup(self.manager.unsubscribe, subscription)
        barrier = threading.Barrier(3)

        def update():
            barrier.wait()
            for index in range(20):
                self.manager._handle_metrics_event("first", {"index": index})

        threads = [threading.Thread(target=update) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
        events = [self.manager.next_subscription_event(subscription, timeout=0) for _ in range(40)]
        revisions = [event["revision"] for event in events]
        self.assertEqual(revisions, sorted(set(revisions)))
        self.assertEqual(revisions[-1], self.manager.list_tasks_snapshot()["revision"])

    def test_restart_has_a_new_instance_and_does_not_run_interrupted_work(self):
        path = self.root / "tasks.json"
        first = TaskManager(path)
        first._tasks["interrupted"] = TaskRecord(
            "interrupted", "paper.pdf", "openai", ["dual"], {}, self.root, status="running"
        )
        first._save_persistent_tasks()
        restored = TaskManager(path)
        before = first.sync_metadata()
        after = restored.list_tasks_snapshot()
        self.assertNotEqual(before["serverInstanceId"], after["serverInstanceId"])
        self.assertEqual(after["tasks"][0]["status"], "incomplete")
        self.assertEqual(after["tasks"][0]["serverInstanceId"], after["serverInstanceId"])
        self.assertLessEqual(after["tasks"][0]["revision"], after["revision"])
        self.assertIsNone(restored._worker)

    def test_quality_updates_are_immutable_versioned_and_survive_restart(self):
        self.manager._persistence_path = self.root / "tasks.json"
        with patch.object(self.manager, "_start_worker_locked"):
            self.create("quality")
        subscription = self.manager.subscribe()
        self.addCleanup(self.manager.unsubscribe, subscription)
        quality = {"selected": 2, "checked": 1, "passed": 0, "corrected": 1,
                   "failed": 0, "unchecked": 1, "notSelected": 5,
                   "requestsUsed": 3, "requestLimit": 8, "paragraphLimit": 20}
        event = {"type": "translation_summary", "translationSummary": {"succeeded": 7},
                 "failedParagraphs": [], "qualitySummary": quality}
        self.manager._handle_progress_event("quality", event, attempt=1)
        snapshot = self.manager.get_task("quality")
        published = self.manager.next_subscription_event(subscription, timeout=0)
        self.assertEqual(snapshot["qualitySummary"], quality)
        self.assertEqual(published["task"]["qualitySummary"], quality)
        self.assertGreater(snapshot["revision"], subscription.initial_events[0]["revision"])
        quality["failed"] = 99
        published["task"]["qualitySummary"]["corrected"] = 99
        self.assertEqual(self.manager.get_task("quality")["qualitySummary"]["failed"], 0)
        self.manager._save_persistent_tasks()
        restored = TaskManager(self.manager._persistence_path)
        self.addCleanup(restored.close)
        self.assertEqual(restored.get_task("quality")["qualitySummary"], snapshot["qualitySummary"])
        self.assertEqual(restored.get_task("quality")["status"], "incomplete")
        with patch.object(restored, "_start_worker_locked"):
            retried = restored.repair_task("quality")
        self.assertIsNone(retried["qualitySummary"])
        revision = retried["revision"]
        restored._handle_progress_event("quality", event, attempt=1)
        self.assertEqual(restored.get_task("quality")["revision"], revision)
        self.assertIsNone(restored.get_task("quality")["qualitySummary"])

    def test_review_attempt_follows_execution_without_mutating_task_options(self):
        attempts = []

        async def translate(payload, task_id, **kwargs):
            attempts.append(payload["review_attempt"])
            result = self.result()
            result.quality_summary = {"requestsUsed": payload["review_attempt"]}
            return result

        self.translator(translate)
        self.create("quality")
        first = self.await_status("quality", "completed")
        self.assertEqual(first["qualitySummary"], {"requestsUsed": 1})
        self.manager.repair_task("quality")
        second = self.await_status("quality", "completed")
        self.assertEqual(second["qualitySummary"], {"requestsUsed": 2})
        self.assertEqual(attempts, [1, 2])
        self.assertNotIn("review_attempt", self.manager._tasks["quality"].request_payload)


if __name__ == "__main__":
    unittest.main()
