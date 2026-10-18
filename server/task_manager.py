from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
import logging
import math
import os
import queue
import shutil
import threading
import uuid
import time
from dataclasses import dataclass, field
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any
from typing import Callable


from pdf2zh_next_service import TranslationOutputFile
from pdf2zh_next_service import diagnose_service_error
from pdf2zh_next_service import explain_service_error
from pdf2zh_next_service import translate_pdf_with_callbacks
from observability import empty_metrics, supports_request_metrics, merge_metrics
from diagnostics import DiagnosticStore, safe_fields, requested_configuration
from task_runtime import run_translation, CleanupFailed, recover_leases

TaskStatus = str
LOGGER = logging.getLogger("zotero_pdf2zh_server.tasks")
MEMORY_RETRY_SECONDS = (2, 5, 15, 60)


class TaskStoreError(OSError):
    public_message = "任务记录无法安全读取，已暂停任务操作并保留原文件。请先备份数据目录，检查或恢复 tasks.json 后重启服务。"

    def __init__(self):
        super().__init__(self.public_message)


class TaskPersistenceError(OSError):
    public_message = "任务变更未能保存，请检查磁盘空间和数据目录权限后重试。取消信号仍会执行；重启后的状态以已保存记录为准。"

    def __init__(self):
        super().__init__(self.public_message)


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass
class TaskRecord:
    task_id: str
    file_name: str
    service: str
    output_modes: list[str]
    request_payload: dict[str, Any]
    workspace_dir: Path
    status: TaskStatus = "queued"
    stage: str | None = None
    stage_current: int = 0
    stage_total: int = 0
    stage_progress: float = 0.0
    overall_progress: float = 0.0
    error: str | None = None
    error_diagnostics: list[dict[str, str]] = field(default_factory=list)
    result_files: dict[str, TranslationOutputFile] = field(default_factory=dict)
    metrics: dict[str, Any] | None = None
    translation_summary: dict | None = None
    quality_summary: dict | None = None
    failed_paragraphs: list = field(default_factory=list)
    attempt: int = 1
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    cancel_requested: bool = False
    cancel_callback: Callable[[], None] | None = field(default=None, repr=False)
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    server_instance_id: str = ""
    revision: int = 0
    progress_detail: dict = field(default_factory=dict)
    submission_fingerprint: str | None = None
    recovery_state: str | None = None
    timing: dict | None = None
    _timer_phase: str | None = field(default=None, repr=False)
    _timer_started: float = field(default=0, repr=False)

    def timing_snapshot(self):
        if self.timing is None:
            return None
        timing = dict(self.timing)
        if self._timer_phase:
            timing[self._timer_phase] += max(0, time.monotonic() - self._timer_started)
        timing["totalSeconds"] = timing["queueSeconds"] + timing["executionSeconds"]
        return timing

    def time_phase(self, phase=None, *, terminal=False, reset=False):
        if reset or self.timing is None:
            self.timing = {"queuedAt": utc_now_iso(), "startedAt": None, "finishedAt": None,
                           "queueSeconds": 0.0, "executionSeconds": 0.0,
                           "complete": reset}
        else:
            self.timing = self.timing_snapshot()
        self._timer_phase = phase
        self._timer_started = time.monotonic()
        if phase == "executionSeconds" and self.timing["startedAt"] is None:
            self.timing["startedAt"] = utc_now_iso()
        if terminal:
            self.timing["finishedAt"] = utc_now_iso()

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "serverInstanceId": self.server_instance_id,
            "revision": self.revision,
            "taskId": self.task_id,
            "fileName": self.file_name,
            "service": self.service,
            "requestedConfiguration": requested_configuration({"service": self.service, **self.request_payload}),
            "outputModes": self.output_modes,
            "status": self.status,
            "stage": self.stage,
            "stageCurrent": self.stage_current,
            "stageTotal": self.stage_total,
            "stageProgress": round(min(100, max(0, self.stage_progress)), 1),
            "overallProgress": round(min(100, max(0, self.overall_progress)), 1),
            "error": self.error,
            "errorDiagnostics": self.error_diagnostics,
            "attempt": self.attempt,
            "translationSummary": self.translation_summary,
            "qualitySummary": self.quality_summary,
            "failedParagraphs": self.failed_paragraphs,
            "canDownloadResult": self.status in {"completed", "incomplete"} and any(
                file.output_path.is_file() for file in self.result_files.values()
            ),
            "canRepair": self.status in {"incomplete", "completed", "cancelled"},
            "resultFiles": {
                output_mode: output_file.filename
                for output_mode, output_file in self.result_files.items()
            },
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "canCancel": self.status in {"queued", "running", "cancelling"} or self.recovery_state is not None,
            "cancelRequested": self.cancel_requested,
            "boundedCancellation": True,
            "recoveryState": self.recovery_state,
            "canResume": self.recovery_state is not None,
            "canRetryMemorySync": self.status in {"completed", "incomplete"} and self.progress_detail.get("translationMemorySync") == "pending",
            "timing": self.timing_snapshot(),
        }
        payload.update(self.progress_detail)
        if self.metrics is not None:
            payload["metrics"] = copy.deepcopy(self.metrics)
            if payload["timing"] is not None:
                payload["metrics"].setdefault("stageDurations", {})["Queue Wait"] = payload["timing"]["queueSeconds"]
        return copy.deepcopy(payload)


@dataclass(eq=False)
class TaskSubscription:
    initial_events: list[dict[str, Any]]
    event_queue: queue.Queue[dict[str, Any]] = field(
        default_factory=lambda: queue.Queue(maxsize=128)
    )
    overflow_revision: int | None = None


class TaskManager:
    def __init__(self, persistence_path: Path | str | None = None, *, translator=None) -> None:
        self._lock = threading.RLock()
        self._persistence_lock = threading.RLock()
        self._tasks: dict[str, TaskRecord] = {}
        self._submissions: dict[str, dict[str, str]] = {}
        self._last_checkpoint = time.monotonic()
        self._subscribers: set[TaskSubscription] = set()
        self._server_instance_id = uuid.uuid4().hex
        self._revision = 0
        self._pending: queue.Queue[tuple[str, int] | None] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._closed = False
        self._memory_condition = threading.Condition(self._lock)
        self._memory_pending = {}
        self._memory_worker = None
        self._persistence_path = (
            Path(persistence_path) if persistence_path is not None else None
        )
        self._snapshot_events = {}
        self._store_error: str | None = None
        self._store_write_failed = False
        self._store_digest: str | None = None
        self._backup_available = False
        self._translator = translator
        diagnostic_root = self._persistence_path.parent / "diagnostics" if self._persistence_path else None
        self.diagnostics = DiagnosticStore(diagnostic_root)
        self._queue_blocked = recover_leases(diagnostic_root) if diagnostic_root and diagnostic_root.exists() else False
        self._load_persistent_tasks()
        if self._persistence_path:
            for task_id in list(self._tasks):
                self._schedule_memory_sync(task_id)

    @staticmethod
    def _has_memory_source(record):
        return record and (record.status == "completed" or
                          (record.status == "incomplete" and record.result_files))

    def _schedule_memory_sync(self, task_id):
        if not self._persistence_path:
            return
        with self._memory_condition:
            record = self._tasks.get(task_id)
            if self._closed or self._store_error or not self._has_memory_source(record):
                return
            self._memory_pending[task_id] = (record.attempt, time.monotonic(), 0)
            if self._memory_worker is None:
                self._memory_worker = threading.Thread(target=self._sync_memories, daemon=True, name="translation-memory-sync")
                self._memory_worker.start()
            self._memory_condition.notify_all()

    def retry_memory_sync(self, task_id):
        with self._persistence_lock, self._lock:
            self.require_task_store()
            record = self._tasks.get(task_id)
            if record is None:
                return None
            if not self._persistence_path or not self._has_memory_source(record):
                raise ValueError("Task has no completed translation to sync")
            previous = record.progress_detail.get("translationMemorySync")
            record.progress_detail["translationMemorySync"] = "pending"
            try:
                self._save_persistent_tasks_locked(strict=True)
            except OSError:
                if previous is None:
                    record.progress_detail.pop("translationMemorySync", None)
                else:
                    record.progress_detail["translationMemorySync"] = previous
                raise
            snapshot = self._task_changed_locked(record)
        self._schedule_memory_sync(task_id)
        return snapshot

    def _sync_memories(self):
        from translation_memory import sync_recovery
        while True:
            with self._memory_condition:
                if self._closed or self._store_error or not self._memory_pending:
                    self._memory_worker = None
                    return
                task_id = min(self._memory_pending, key=lambda key: self._memory_pending[key][1])
                attempt, due, retries = self._memory_pending[task_id]
                if due > time.monotonic():
                    self._memory_condition.wait(due - time.monotonic())
                    continue
                self._memory_pending.pop(task_id)
                record = self._tasks.get(task_id)
                if not self._has_memory_source(record) or record.attempt != attempt:
                    continue
                payload = copy.deepcopy(record.request_payload)
            try:
                sync_recovery(self._persistence_path.parent / "translation-memory.sqlite3", payload, timeout=1)
                state = "synced"
            except Exception as exc:
                state = "pending"
                if retries == 0:
                    LOGGER.warning("[%s] translation memory sync deferred (%s); retrying in background", task_id, type(exc).__name__)
            changed = False
            with self._memory_condition:
                record = self._tasks.get(task_id)
                if self._closed or not self._has_memory_source(record) or record.attempt != attempt:
                    continue
                previous = record.progress_detail.get("translationMemorySync")
                if previous != state and (previous is not None or state == "pending"):
                    record.progress_detail["translationMemorySync"] = state
                    self._task_changed_locked(record)
                    changed = True
                if state == "pending":
                    delay = MEMORY_RETRY_SECONDS[min(retries, len(MEMORY_RETRY_SECONDS) - 1)]
                    self._memory_pending.setdefault(task_id, (attempt, time.monotonic() + delay, retries + 1))
            if changed:
                self._save_persistent_tasks()

    def list_tasks(self) -> list[dict[str, Any]]:
        with self._lock:
            records = sorted(
                self._tasks.values(), key=lambda record: record.created_at, reverse=True
            )
            return [record.to_dict() for record in records]

    def sync_metadata(self) -> dict[str, Any]:
        with self._lock:
            return {"serverInstanceId": self._server_instance_id, "revision": self._revision}

    def task_stats(self):
        with self._lock:
            return {"total": len(self._tasks),
                    "active": sum(record.status in {"queued", "running", "cancelling"} for record in self._tasks.values()),
                    "failed": sum(record.status == "failed" for record in self._tasks.values()),
                    "completed": sum(record.status == "completed" for record in self._tasks.values())}

    def list_tasks_snapshot(self) -> dict[str, Any]:
        with self._lock:
            self.require_task_store()
            return {**self.sync_metadata(), "tasks": self.list_tasks()}

    @staticmethod
    def _is_active(record):
        return record.status in {"queued", "running", "cancelling"} or record.recovery_state is not None

    def list_tasks_page(self, *, limit=30, cursor=None, tracked=None):
        """Active work is never paged. History uses a stable (createdAt, id) boundary."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("History limit must be between 1 and 100")
        if not isinstance(tracked, (list, type(None))) or (tracked and (len(tracked) > 10000 or any(
                not isinstance(value, str) or not value or len(value) > 128 for value in tracked))):
            raise ValueError("Invalid tracked task IDs")
        boundary = None
        with self._lock:
            self.require_task_store()
            if cursor is not None:
                try:
                    if not isinstance(cursor, str) or len(cursor) > 1024:
                        raise ValueError()
                    instance, created, task_id = json.loads(base64.urlsafe_b64decode(cursor))
                    if not all(isinstance(value, str) for value in (instance, created, task_id)) or not instance:
                        raise ValueError()
                except (ValueError, TypeError):
                    raise ValueError("Invalid history cursor") from None
                if instance != self._server_instance_id:
                    raise ValueError("History cursor expired")
                boundary = (created, task_id)
            active = [record for record in self._tasks.values() if self._is_active(record)]
            history = sorted((record for record in self._tasks.values() if not self._is_active(record)),
                             key=lambda record: (record.created_at, record.task_id), reverse=True)
            candidates = [record for record in history if boundary is None or (record.created_at, record.task_id) < boundary]
            page = candidates[:limit]
            more = len(candidates) > limit
            next_cursor = (base64.urlsafe_b64encode(json.dumps([self._server_instance_id, page[-1].created_at,
                           page[-1].task_id]).encode()).decode() if page else cursor or
                           base64.urlsafe_b64encode(json.dumps([self._server_instance_id, "", ""]).encode()).decode())
            visible = {record.task_id: record for record in [*active, *page]}
            records = dict(visible)
            for task_id in tracked or []:
                if task_id in self._tasks:
                    records[task_id] = self._tasks[task_id]
            return {**self.sync_metadata(), "scope": "dashboard", "tasks": [record.to_dict() for record in records.values()],
                    "visibleTaskIds": list(visible), "missingTaskIds": [value for value in tracked or [] if value not in self._tasks],
                    "pagination": {"nextCursor": next_cursor, "hasMore": more, "totalHistory": len(history), "limit": limit}}

    def task_store_health(self) -> dict[str, Any]:
        return {
            "status": "blocked" if self._store_error else "ok",
            "reason": self._store_error,
            "backupAvailable": self._backup_available,
            "writeFailed": self._store_write_failed,
            **({"message": TaskStoreError.public_message} if self._store_error else {}),
        }

    def require_task_store(self) -> None:
        if self._store_error:
            raise TaskStoreError()

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._tasks.get(task_id)
            if record is None:
                return None
            return record.to_dict()

    def create_task(
        self,
        *,
        task_id: str,
        file_name: str,
        service: str,
        output_modes: list[str],
        request_payload: dict[str, Any],
        workspace_dir: Path,
        request_id: str | None = None,
        dedup_scope: str = "",
    ) -> dict[str, Any]:
        fingerprint = None
        if request_id:
            # Credentials affect identity but only their enclosing digest is indexed.
            options = {k: v for k, v in request_payload.items() if k not in {"input_path", "output_dir", "translation_memory_path"}}
            with Path(request_payload["input_path"]).open("rb") as source:
                file_hash = hashlib.file_digest(source, "sha256").hexdigest()
            fingerprint = hashlib.sha256(json.dumps(
                [file_hash, service, sorted(output_modes), options, dedup_scope],
                sort_keys=True, ensure_ascii=False, separators=(",", ":"),
            ).encode()).hexdigest()
        record = TaskRecord(
            task_id=task_id,
            file_name=file_name,
            service=service,
            output_modes=output_modes,
            request_payload=request_payload,
            workspace_dir=workspace_dir,
            metrics=empty_metrics() if supports_request_metrics(service) else None,
            submission_fingerprint=fingerprint,
        )
        record.time_phase("queueSeconds", reset=True)
        # Persist acceptance before making work visible to the execution thread.
        with self._persistence_lock, self._lock:
            self._ensure_open_locked()
            if request_id in self._submissions:
                submission = self._submissions[request_id]
                if submission["fingerprint"] != fingerprint:
                    raise ValueError("提交编号已用于不同文件或配置，请重新提交")
                existing = self._tasks.get(submission["taskId"])
                if existing is None:
                    raise ValueError("此提交对应的任务已删除，请使用新的提交编号")
                return existing.to_dict()
            existing = next((task for task in self._tasks.values()
                             if fingerprint and task.submission_fingerprint == fingerprint
                             and (task.status in {"queued", "running", "cancelling"} or task.recovery_state)), None)
            selected = existing or record
            if request_id:
                self._submissions[request_id] = {"fingerprint": fingerprint, "taskId": selected.task_id}
            if existing is None:
                self._tasks[task_id] = record
            try:
                self._save_persistent_tasks_locked(strict=True)
            except OSError:
                if request_id:
                    self._submissions.pop(request_id, None)
                if existing is None:
                    self._tasks.pop(task_id, None)
                raise
            if existing is not None:
                return existing.to_dict()
            snapshot = self._task_changed_locked(record)
            self._pending.put_nowait((task_id, record.attempt))
        LOGGER.info(
            "[%s] task queued: file=%s service=%s output_modes=%s workspace=%s",
            task_id,
            file_name,
            service,
            ",".join(output_modes),
            workspace_dir,
        )
        with self._lock:
            self._start_worker_locked()
        return snapshot

    def subscribe(self, *, active_only=False) -> TaskSubscription:
        with self._lock:
            subscription = TaskSubscription(
                initial_events=[
                    {
                        "type": "snapshot", "task": snapshot,
                        "serverInstanceId": self._server_instance_id,
                        "revision": snapshot["revision"],
                    }
                    for snapshot in (record.to_dict() for record in self._tasks.values()
                                     if not active_only or self._is_active(record))
                ]
            )
            self._subscribers.add(subscription)
            return subscription

    def unsubscribe(self, subscription: TaskSubscription) -> None:
        with self._lock:
            self._subscribers.discard(subscription)

    def subscription_resync(self, subscription: TaskSubscription) -> dict[str, Any] | None:
        with self._lock:
            if subscription.overflow_revision is None:
                return None
            return {"type": "resync", **self.sync_metadata()}

    def next_subscription_event(
        self, subscription: TaskSubscription, *, timeout: float = 15
    ) -> dict[str, Any]:
        resync = self.subscription_resync(subscription)
        if resync is not None:
            return resync
        event = subscription.event_queue.get(timeout=timeout)
        return self.subscription_resync(subscription) or event

    def cancel_task(self, task_id: str) -> dict[str, Any] | None:
        cancel_callback = None
        try:
            with self._persistence_lock, self._lock:
                self.require_task_store()
                record = self._tasks.get(task_id)
                if record is None:
                    return None
                if record.status not in {"queued", "running", "cancelling"} and not record.recovery_state:
                    if record.cancel_requested:
                        self._save_persistent_tasks_locked(strict=True)
                    return record.to_dict()
                recovering = record.recovery_state is not None
                record.recovery_state = None
                record.progress_detail.setdefault("cancelPhase", "requested")
                if not record.cancel_event.is_set():
                    record.cancel_event.requested_at = time.monotonic()
                record.cancel_requested = True
                record.cancel_event.set()
                record.status = "cancelled" if record.status == "queued" or recovering else "cancelling"
                if record.status == "cancelled":
                    record.time_phase(terminal=True)
                    record.stage = "cancelled"
                    record.progress_detail.update(cancelPhase="cancelled", cancelReason="user")
                    record.error = None
                    record.error_diagnostics = []
                cancel_callback = record.cancel_callback
                # Stopping work is irreversible: keep the signal even if the disk
                # fails, but never acknowledge an unsaved cancellation as durable.
                try:
                    self._save_persistent_tasks_locked(strict=True)
                finally:
                    snapshot = self._task_changed_locked(record)
                LOGGER.info("[%s] cancellation requested", task_id)
                return snapshot

        finally:
            if cancel_callback is not None:
                try:
                    cancel_callback()
                except Exception:
                    LOGGER.warning("[%s] cancellation callback failed; cancellation signal remains set", task_id)

    def delete_task(self, task_id: str) -> dict[str, Any] | None:
        with self._persistence_lock, self._lock:
            self.require_task_store()
            record = self._tasks.get(task_id)
            if record is None:
                return None
            if record.status in {"queued", "running", "cancelling"}:
                raise ValueError("Active task cannot be deleted")
            if self._has_memory_source(record) and record.progress_detail.get("translationMemorySync") == "pending":
                raise ValueError("Translation memory is still pending; retry sync before deleting this task")
            deleted = self._tasks.pop(task_id)
            try:
                self._save_persistent_tasks_locked(strict=True)
            except OSError:
                self._tasks[task_id] = deleted
                raise
            self._deleted_locked(deleted)
            snapshot = deleted.to_dict()

        shutil.rmtree(deleted.workspace_dir, ignore_errors=True)
        LOGGER.info("[%s] task deleted", task_id)
        return snapshot

    def clear_failed_tasks(self) -> int:
        with self._persistence_lock, self._lock:
            self.require_task_store()
            failed_task_ids = [
                task_id
                for task_id, record in self._tasks.items()
                if record.status == "failed"
            ]
            deleted_records = [self._tasks.pop(task_id) for task_id in failed_task_ids]
            try:
                self._save_persistent_tasks_locked(strict=True)
            except OSError:
                self._tasks.update((record.task_id, record) for record in deleted_records)
                raise
            for record in deleted_records:
                self._deleted_locked(record)

        for record in deleted_records:
            shutil.rmtree(record.workspace_dir, ignore_errors=True)

        if failed_task_ids:
            LOGGER.info("cleared failed tasks: %s", ",".join(failed_task_ids))
        return len(failed_task_ids)

    def repair_task(self, task_id: str) -> dict[str, Any] | None:
        return self.retry_task(task_id, repair=True)

    def resume_task(self, task_id: str) -> dict[str, Any] | None:
        with self._persistence_lock, self._lock:
            self._ensure_open_locked()
            record = self._tasks.get(task_id)
            if record is None:
                return None
            if record.recovery_state is None:
                # A repeated resume request must not execute the task twice.
                return record.to_dict()
            if not Path(record.request_payload.get("input_path", "")).is_file():
                raise ValueError("Task input file is no longer available")
            previous = self._record_to_persistence(record)
            record.recovery_state = None
            record.status = "queued"
            record.stage = None
            record.error = None
            record.error_diagnostics = []
            record.progress_detail = {}
            record.cancel_requested = False
            record.cancel_event = threading.Event()
            record.cancel_callback = None
            record.time_phase("queueSeconds")
            try:
                self._save_persistent_tasks_locked(strict=True)
            except OSError:
                restored = self._record_from_persistence(previous)
                restored.server_instance_id = record.server_instance_id
                restored.revision = record.revision
                self._tasks[task_id] = restored
                raise
            snapshot = self._task_changed_locked(record)
            self._pending.put_nowait((task_id, record.attempt))
            self._start_worker_locked()
            return snapshot

    def retry_task(self, task_id: str, *, repair=False) -> dict[str, Any] | None:
        with self._persistence_lock, self._lock:
            self._ensure_open_locked()
            record = self._tasks.get(task_id)
            if record is None:
                return None
            if record.status in {"queued", "running", "cancelling"}:
                raise ValueError("Active task cannot be retried")
            allowed = {"incomplete", "completed", "cancelled"} if repair else {"failed"}
            if record.status not in allowed:
                raise ValueError("Task is not eligible for repair" if repair else "Only failed tasks can be retried")

            input_path = Path(record.request_payload["input_path"])
            if not input_path.exists():
                raise ValueError("Task input file is no longer available")

            previous = record
            record = copy.copy(previous)
            record.request_payload = copy.deepcopy(previous.request_payload)
            # A rejected retry must not destroy the previous attempt's files.
            output_dir = record.workspace_dir / f"{'repair' if repair else 'retry'}-{record.attempt + 1}"
            if repair:
                record.request_payload.update(qps=2, pool_size=4, repair_attempt=record.attempt + 1)
            output_dir.mkdir(parents=True, exist_ok=True)
            record.request_payload["output_dir"] = str(output_dir)

            record.status = "queued"
            record.recovery_state = None
            record.time_phase("queueSeconds", reset=True)
            record.stage = None
            record.stage_current = 0
            record.stage_total = 0
            record.stage_progress = 0.0
            record.overall_progress = 0.0
            record.error = None
            record.error_diagnostics = []
            record.result_files = {}
            record.translation_summary = None
            record.quality_summary = None
            record.failed_paragraphs = []
            record.metrics = empty_metrics() if supports_request_metrics(record.service) else None
            record.attempt += 1
            record.cancel_requested = False
            record.progress_detail = {}
            record.cancel_callback = None
            record.cancel_event = threading.Event()
            self._tasks[task_id] = record
            try:
                self._save_persistent_tasks_locked(strict=True)
            except OSError:
                self._tasks[task_id] = previous
                # Only remove the newly prepared empty directory.
                try:
                    output_dir.rmdir()
                except OSError:
                    pass
                raise
            snapshot = self._task_changed_locked(record)
            self._pending.put_nowait((task_id, record.attempt))
            self._start_worker_locked()
            LOGGER.info("[%s] task retry queued", task_id)
            return snapshot

    def get_result_file(
        self,
        task_id: str,
        output_mode: str | None = None,
    ) -> tuple[TaskRecord, TranslationOutputFile | None] | None:
        with self._lock:
            record = self._tasks.get(task_id)
            if record is None:
                return None
            if record.status not in {"completed", "incomplete"} or not record.result_files:
                return record, None

            selected_output_mode = output_mode
            if selected_output_mode is None:
                if len(record.result_files) != 1:
                    return record, None
                selected_output_mode = next(iter(record.result_files))

            result_file = record.result_files.get(selected_output_mode)
            if result_file is None:
                return record, None
            if not result_file.output_path.exists():
                return record, None
            return record, result_file

    def _ensure_open_locked(self) -> None:
        self.require_task_store()
        if self._closed:
            raise ValueError("Task manager is shutting down")
        if self._queue_blocked:
            raise ValueError("CANCEL_CLEANUP_FAILED: 服务需要恢复，队列已暂停")

    def _start_worker_locked(self) -> None:
        if self._closed or self._worker is not None:
            return
        self._worker = threading.Thread(
            target=self._work, daemon=True, name="pdf2zh-task-worker"
        )
        self._worker.start()

    def _work(self) -> None:
        while True:
            pending = self._pending.get()
            try:
                if pending is None:
                    return
                if self._queue_blocked:
                    return
                self._run_task(*pending)
            finally:
                self._pending.task_done()

    def close(self, *, timeout: float | None = 17) -> None:
        callbacks = []
        changed = False
        with self._lock:
            if not self._closed:
                self._closed = True
                self._memory_condition.notify_all()
                for record in self._tasks.values():
                    if record.status not in {"queued", "running", "cancelling"}:
                        continue
                    changed = True
                    if not record.cancel_event.is_set():
                        record.cancel_event.requested_at = time.monotonic()
                    record.cancel_event.set()
                    if record.status == "queued":
                        record.status = "paused"
                        record.stage = "paused"
                        record.recovery_state = "queued"
                        record.time_phase()
                    else:
                        record.status = "cancelling"
                    self._task_changed_locked(record)
                    if record.cancel_callback is not None:
                        callbacks.append(record.cancel_callback)
                if self._worker is not None:
                    self._pending.put_nowait(None)
            worker = self._worker
        for callback in callbacks:
            callback()
        if worker is not None:
            worker.join(timeout=17 if timeout is None else timeout)
            if worker.is_alive():
                raise RuntimeError("Translation worker did not stop before shutdown timeout")
        if changed:
            self._save_persistent_tasks()
        memory_worker = self._memory_worker
        if memory_worker is not None:
            memory_worker.join(timeout=5)

    def _run_task(self, task_id: str, attempt: int | None = None) -> None:
        with self._lock:
            record = self._tasks.get(task_id)
            if self._closed or record is None or record.recovery_state or (
                attempt is not None
                and (record.attempt != attempt or record.status != "queued")
            ):
                return
            attempt = record.attempt
            baseline_metrics = copy.deepcopy(record.metrics)
            record.progress_detail = {"lastProgressAt": utc_now_iso(), "operation": "initialization", "idleSeconds": 0, **record.progress_detail}
            record.status = "cancelling" if record.cancel_requested else "running"
            record.time_phase("executionSeconds")
            request_payload = copy.deepcopy(record.request_payload)
            request_payload["review_attempt"] = attempt
            if self._persistence_path:
                request_payload["translation_memory_path"] = str(self._persistence_path.parent / "translation-memory.sqlite3")
            self.diagnostics.record("task_configuration", {
                "taskId": task_id, "attempt": attempt, "serverInstanceId": self._server_instance_id,
                "requestedConfiguration": requested_configuration(request_payload),
            })
            cancel_event = record.cancel_event
            self._task_changed_locked(record)

        snapshot_events = (threading.Event(), threading.Event())
        with self._lock:
            self._snapshot_events[task_id] = snapshot_events
        self.diagnostics.active.add(task_id)
        progress = lambda event: self._handle_progress_event(task_id, event, attempt=attempt)
        metrics = lambda value: self._handle_metrics_event(task_id, value, attempt=attempt, baseline=baseline_metrics)
        try:
            with self._persistence_lock:
                self._save_persistent_tasks_locked(strict=True)
            if self._translator is not None:
                # Explicit test dependency; production always uses the isolated runner.
                result = asyncio.run(self._translator(request_payload, task_id,
                    cancel_event=cancel_event, progress_callback=progress, metrics_callback=metrics,
                    on_config_ready=lambda config: self._register_cancel_callback(task_id, config.cancel_translation, attempt=attempt)))
            else:
                result = run_translation(request_payload, task_id, cancel_event=cancel_event,
                    progress_callback=progress, metrics_callback=metrics,
                    runtime_callback=lambda detail: self._handle_runtime_event(task_id, attempt, detail),
                    store=self.diagnostics, lease_dir=self.diagnostics.root, snapshot_events=snapshot_events,
                    identity={"taskId": task_id, "attempt": attempt, "serverInstanceId": self._server_instance_id})
        except (Exception, asyncio.CancelledError) as exc:
            if isinstance(exc, CleanupFailed):
                with self._lock:
                    self._queue_blocked = True
                    record.status = "failed"
                    record.time_phase(terminal=True)
                    record.timing["complete"] = False
                    record.error = "CANCEL_CLEANUP_FAILED: 无法确认进程退出，队列已暂停，请恢复服务"
                    record.progress_detail.update(cancelPhase="cleanup_failed", queueBlocked=True)
                    self._task_changed_locked(record)
                self._save_persistent_tasks()
            else:
                self._handle_task_error(task_id, exc, attempt=attempt)
            return
        finally:
            with self._lock:
                self._snapshot_events.pop(task_id, None)
            if not self._queue_blocked:
                self.diagnostics.active.discard(task_id)

        with self._lock:
            record = self._tasks.get(task_id)
            if record is None or record.attempt != attempt:
                return
            record.translation_summary = result.translation_summary
            memory_sync = getattr(result, "translation_memory_sync", None)
            if memory_sync:
                record.progress_detail["translationMemorySync"] = memory_sync
            record.quality_summary = getattr(result, "quality_summary", record.quality_summary)
            record.failed_paragraphs = result.failed_paragraphs
            incomplete = bool(result.translation_summary and (result.translation_summary.get("failed", 0) or result.translation_summary.get("pending", 0)))
            cancelled = record.cancel_event.is_set()
            record.result_files = {} if cancelled else dict(result.files)
            if cancelled:
                record.progress_detail.pop("translationMemorySync", None)
                self._finish_cancellation_locked(record)
            else:
                record.status = "incomplete" if incomplete else "completed"
                record.stage = record.status
                record.error = f"未完成，剩余 {result.translation_summary.get('failed', 0) + result.translation_summary.get('pending', 0)} 段，可补译" if incomplete else None
                record.stage_progress = 99.0 if incomplete else 100.0
                record.overall_progress = 99.0 if incomplete else 100.0
                record.time_phase(terminal=True)
            record.cancel_callback = None
            self._task_changed_locked(record)

        self._save_persistent_tasks()
        self._schedule_memory_sync(task_id)
        LOGGER.info(
            "[%s] task %s: %s",
            task_id,
            record.status,
            ", ".join(file.filename for file in result.files.values()),
        )

    def _handle_runtime_event(self, task_id, attempt, detail):
        with self._lock:
            record = self._tasks.get(task_id)
            if record is None or record.attempt != attempt or record.status not in {"running", "cancelling"}:
                return
            if isinstance(detail.get("heartbeatAt"), (float, int)):
                detail = {**detail, "heartbeatAt": datetime.fromtimestamp(detail["heartbeatAt"], UTC).isoformat()}
            record.progress_detail.update(safe_fields(detail))
            self._task_changed_locked(record)
        # Bound lost timing on an abrupt exit; counters never include downtime.
        if time.monotonic() - self._last_checkpoint >= 5:
            self._save_persistent_tasks()

    def export_diagnostics(self, task_id=None):
        with self._lock:
            if task_id and task_id not in self._tasks:
                return None
            tasks = [safe_fields(r.to_dict()) for r in self._tasks.values() if not task_id or r.task_id == task_id][-2000:]
            snapshot_events = self._snapshot_events.get(task_id) if task_id else next(iter(self._snapshot_events.values()), None)
        if snapshot_events:
            snapshot_events[1].clear(); snapshot_events[0].set()
            snapshot_events[1].wait(timeout=1.1)
        payload = self.diagnostics.export(task_id)
        payload.update(tasks=tasks, queueBlocked=self._queue_blocked,
                       limitations=["Latest stack may be unavailable while native code holds the interpreter; saved snapshots are included."])
        return payload

    def _register_cancel_callback(
        self,
        task_id: str,
        cancel_callback: Callable[[], None],
        *,
        attempt: int | None = None,
    ) -> None:
        should_cancel_immediately = False
        with self._lock:
            record = self._tasks.get(task_id)
            if record is None or (attempt is not None and record.attempt != attempt):
                return
            record.cancel_callback = cancel_callback
            should_cancel_immediately = record.cancel_event.is_set()

        if should_cancel_immediately:
            cancel_callback()

    def _handle_progress_event(
        self, task_id: str, event: dict[str, Any], *, attempt: int | None = None
    ) -> None:
        event_type = str(event.get("type") or "")
        with self._lock:
            record = self._tasks.get(task_id)
            if record is None or (attempt is not None and record.attempt != attempt):
                return
            if record.status not in {"queued", "running", "cancelling"}:
                return
            if record.status == "queued":
                record.status = "running"
                record.time_phase("executionSeconds")

            advances = event_type != "progress_update" or (
                event.get("stage", record.stage) != record.stage
                or event.get("stage_current", record.stage_current) != record.stage_current
                or event.get("stage_total", record.stage_total) != record.stage_total
            )
            if advances and event_type in {"parse_detail", "progress_start", "progress_update", "progress_end", "translation_summary"}:
                record.progress_detail.update(lastProgressAt=utc_now_iso(), idleSeconds=0, stalled=False)
            if event_type == "parse_detail":
                record.progress_detail.update(safe_fields(event))
            if event_type == "diagnostic_configuration":
                configuration = safe_fields(event.get("effectiveConfiguration", {}))
                record.progress_detail["effectiveConfiguration"] = configuration
                self.diagnostics.record("task_configuration", {
                    "taskId": task_id, "attempt": record.attempt, "serverInstanceId": self._server_instance_id,
                    "effectiveConfiguration": configuration,
                })
            if event_type in {"progress_start", "progress_update", "progress_end"}:
                record.stage = str(event.get("stage") or record.stage or "unknown")
                record.stage_current = self._coerce_int(
                    event.get("stage_current"),
                    record.stage_current,
                )
                record.stage_total = self._coerce_int(
                    event.get("stage_total"),
                    record.stage_total,
                )
                record.stage_progress = self._coerce_float(
                    event.get("stage_progress"),
                    record.stage_progress,
                )
                record.overall_progress = self._coerce_float(
                    event.get("overall_progress"),
                    record.overall_progress,
                )

            record.overall_progress = min(99.0, record.overall_progress)
            if event_type == "translation_summary":
                record.translation_summary = event["translationSummary"]
                record.failed_paragraphs = event["failedParagraphs"]
                if "qualitySummary" in event:
                    record.quality_summary = copy.deepcopy(event["qualitySummary"])
            if event_type == "error":
                record.error = explain_service_error(
                    str(event.get("error") or "translation failed")
                )
                record.error_diagnostics = diagnose_service_error(record.error)

            self._task_changed_locked(record)

    def _handle_metrics_event(
        self,
        task_id: str,
        metrics: dict[str, Any],
        *,
        attempt: int | None = None,
        baseline: dict | None = None,
    ) -> None:
        with self._lock:
            record = self._tasks.get(task_id)
            if record is None or (attempt is not None and record.attempt != attempt):
                return
            if record.status not in {"queued", "running", "cancelling"}:
                return
            combined = merge_metrics(baseline, metrics) if baseline is not None else copy.deepcopy(metrics)
            if combined.get("requests") and combined.get("requests") != (record.metrics or {}).get("requests"):
                record.progress_detail["lastRequestAt"] = utc_now_iso()
            record.metrics = combined
            self._task_changed_locked(record)

    def _handle_task_error(
        self, task_id: str, exc: BaseException, *, attempt: int | None = None
    ) -> None:
        with self._lock:
            record = self._tasks.get(task_id)
            if record is None or (attempt is not None and record.attempt != attempt):
                return

            error_message = explain_service_error(str(exc) or exc.__class__.__name__)
            cancelled = record.cancel_event.is_set() or isinstance(exc, asyncio.CancelledError)
            if cancelled:
                self._finish_cancellation_locked(record)
            else:
                record.status = "failed"
                record.time_phase(terminal=True)
                record.stage = "failed"
                record.error = error_message
                record.error_diagnostics = diagnose_service_error(error_message)
            record.cancel_callback = None
            self._task_changed_locked(record)

        self._save_persistent_tasks()
        if cancelled:
            LOGGER.info("[%s] task %s", task_id, record.status)
            return
        LOGGER.error(
            "[%s] task failed: error_type=%s",
            task_id,
            type(exc).__name__,
        )

    def _finish_cancellation_locked(self, record: TaskRecord) -> None:
        # Shutdown stops execution without changing the user's cancellation intent.
        interrupted = self._closed and not record.cancel_requested
        record.status = "incomplete" if interrupted else "cancelled"
        record.recovery_state = "interrupted" if interrupted else None
        record.time_phase(terminal=not interrupted)
        record.progress_detail["cancelPhase"] = "cancelled"
        if interrupted:
            record.progress_detail["cancelReason"] = "shutdown"
        record.stage = record.status
        record.error = "服务关闭中断了翻译，可点击继续任务" if interrupted else None
        record.error_diagnostics = []
        record.overall_progress = min(99.0, record.overall_progress)

    def _task_changed_locked(self, record: TaskRecord) -> dict[str, Any]:
        signature = (record.status, record.stage, record.stage_current, record.progress_detail.get("currentPage"), record.progress_detail.get("cancelPhase"), record.progress_detail.get("operation"))
        if getattr(record, "_diagnostic_signature", None) != signature:
            record._diagnostic_signature = signature
            self.diagnostics.record("task_state", {
                key: value for key, value in record.to_dict().items()
                if key not in {"metrics", "failedParagraphs"}
            })
        self._revision += 1
        record.server_instance_id = self._server_instance_id
        record.revision = self._revision
        record.updated_at = utc_now_iso()
        snapshot = record.to_dict()
        self._publish_event_locked({"type": "task", "task": snapshot, **self.sync_metadata()})
        return snapshot

    def _deleted_locked(self, record: TaskRecord) -> None:
        self._revision += 1
        record.server_instance_id = self._server_instance_id
        record.revision = self._revision
        self._publish_event_locked({
            "type": "deleted", "taskId": record.task_id, **self.sync_metadata()
        })

    def _publish_event_locked(self, event: dict[str, Any]) -> None:
        for subscription in self._subscribers:
            if subscription.overflow_revision is not None:
                subscription.overflow_revision = self._revision
                continue
            try:
                subscription.event_queue.put_nowait(copy.deepcopy(event))
            except queue.Full:
                subscription.overflow_revision = self._revision

    def _load_persistent_tasks(self) -> None:
        if self._persistence_path is None:
            return

        raw = None
        try:
            raw = self._persistence_path.read_bytes()
            records, submissions = self._decode_task_store(raw)
        except FileNotFoundError:
            # A backup without a primary file is an interrupted/lost store, not a new queue.
            if self._persistence_path.with_suffix(".json.bak").exists():
                self._block_task_store("missing")
            return
        except OSError:
            self._block_task_store("unreadable")
            return
        except (ValueError, TypeError, KeyError, AttributeError):
            self._block_task_store("invalid", raw)
            return

        self._store_digest = hashlib.sha256(raw).hexdigest()
        self._submissions = submissions
        for record in records:
            if record.status in {"queued", "running", "cancelling"}:
                if record.timing is not None:
                    record.timing["complete"] = False
                if record.cancel_requested:
                    record.status = "cancelled"
                    record.recovery_state = None
                    record.error = None
                else:
                    record.recovery_state = "queued" if record.status == "queued" else "interrupted"
                    record.status = "paused" if record.status == "queued" else "incomplete"
                    record.error = "服务重启，等待继续上次队列"
                record.stage = record.status
                record.overall_progress = min(99.0, record.overall_progress)
            elif record.status == "incomplete" and record.progress_detail.get("cancelReason") == "shutdown" and not record.cancel_requested:
                record.recovery_state = "interrupted"
            self._revision += 1
            record.server_instance_id = self._server_instance_id
            record.revision = self._revision
            self._tasks[record.task_id] = record

    @classmethod
    def _decode_task_store(cls, raw: bytes) -> tuple[list[TaskRecord], dict[str, Any]]:
        def invalid_constant(_value):
            raise ValueError("Non-finite number in task store")
        payload = json.loads(raw, parse_constant=invalid_constant)
        if (not isinstance(payload, dict) or payload.get("schemaVersion", 1) != 1
                or not isinstance(payload.get("tasks"), list)
                or not isinstance(payload.get("submissions", {}), dict)):
            raise ValueError("Invalid task store schema")
        records, seen = [], set()
        for row in payload["tasks"]:
            if (not isinstance(row, dict)
                    or any(not isinstance(row.get(key), str) or not row[key]
                           for key in ("task_id", "file_name", "service", "workspace_dir"))
                    or row["task_id"] in seen
                    or not isinstance(row.get("request_payload", {}), dict)
                    or not isinstance(row.get("output_modes", []), list)
                    or any(not isinstance(mode, str) for mode in row.get("output_modes", []))
                    or row.get("status", "failed") not in {
                        "queued", "running", "cancelling", "cancelled", "completed", "failed", "incomplete", "paused"}):
                raise ValueError("Invalid task record")
            for key in ("result_files", "metrics", "progress_detail", "timing", "translation_summary", "quality_summary"):
                if row.get(key) is not None and not isinstance(row[key], dict):
                    raise ValueError("Invalid task record field")
            for key in ("failed_paragraphs", "error_diagnostics"):
                if row.get(key) is not None and not isinstance(row[key], list):
                    raise ValueError("Invalid task record list")
            for output in (row.get("result_files") or {}).values():
                if (not isinstance(output, dict)
                        or any(not isinstance(output.get(key), str) or not output[key]
                               for key in ("output_path", "filename"))):
                    raise ValueError("Invalid result file record")
            timing = row.get("timing") or {}
            for key in ("queueSeconds", "executionSeconds", "totalSeconds"):
                value = timing.get(key, 0)
                if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                    raise ValueError("Invalid task timing")
            record = cls._record_from_persistence(row)
            if record is None:
                raise ValueError("Unreadable task record")
            seen.add(record.task_id)
            records.append(record)
        submissions = payload.get("submissions", {})
        for key, value in submissions.items():
            if (not key or not isinstance(value, dict)
                    or any(not isinstance(value.get(field), str) or not value[field]
                           for field in ("taskId", "fingerprint"))):
                raise ValueError("Invalid submission index")
        return records, submissions

    def _block_task_store(self, reason: str, raw: bytes | None = None) -> None:
        self._store_error = reason
        LOGGER.error("Task store is blocked (%s); original file will not be overwritten", reason)
        if raw is not None:
            # Keep the primary untouched, including when the recovery copy cannot be written.
            recovery = self._persistence_path.with_name(
                self._persistence_path.name + ".recovery-" + hashlib.sha256(raw).hexdigest()[:16])
            try:
                with recovery.open("xb") as stream:
                    os.chmod(recovery, 0o600)
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
            except OSError:
                LOGGER.warning("Could not create task store recovery copy (it may already exist)")
        try:
            self._decode_task_store(self._persistence_path.with_suffix(".json.bak").read_bytes())
            self._backup_available = True
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            self._backup_available = False

    def _save_persistent_tasks(self) -> None:
        with self._persistence_lock:
            self._save_persistent_tasks_locked()

    def _save_persistent_tasks_locked(self, *, strict=False) -> None:
        if self._persistence_path is None:
            return

        with self._lock:
            records = copy.deepcopy([
                self._record_to_persistence(record)
                for record in self._tasks.values()
            ])

            submissions = copy.deepcopy(self._submissions)
        payload = {"schemaVersion": 1, "tasks": records, "submissions": submissions}
        try:
            self.require_task_store()
            self._persistence_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                previous = self._persistence_path.read_bytes()
            except FileNotFoundError:
                if self._store_digest is not None:
                    self._block_task_store("missing")
                    raise TaskStoreError()
                previous = None
            except OSError:
                self._block_task_store("unreadable")
                raise TaskStoreError() from None
            if previous is not None and hashlib.sha256(previous).hexdigest() != self._store_digest:
                self._block_task_store("changed", previous)
                raise TaskStoreError()
            encoded = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
            if previous is not None:
                backup = self._persistence_path.with_suffix(".json.bak")
                self._write_snapshot(backup, previous)
                self._backup_available = True
            self._write_snapshot(self._persistence_path, encoded)
            self._store_digest = hashlib.sha256(encoded).hexdigest()
            self._store_write_failed = False
            self._last_checkpoint = time.monotonic()
        except OSError as exc:
            self._store_write_failed = True
            LOGGER.warning("failed to save persisted tasks: %s", exc)
            if strict:
                if isinstance(exc, TaskStoreError):
                    raise
                raise TaskPersistenceError() from exc

    @staticmethod
    def _write_snapshot(path: Path, content: bytes) -> None:
        temp_path = path.with_suffix(path.suffix + ".tmp")
        try:
            with temp_path.open("wb") as stream:
                os.chmod(temp_path, 0o600)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            temp_path.replace(path)
        finally:
            temp_path.unlink(missing_ok=True)

    @staticmethod
    def _record_to_persistence(record: TaskRecord) -> dict[str, Any]:
        return {
            "task_id": record.task_id,
            "file_name": record.file_name,
            "service": record.service,
            "output_modes": record.output_modes,
            "request_payload": record.request_payload,
            "workspace_dir": str(record.workspace_dir),
            "status": record.status,
            "stage": record.stage,
            "stage_current": record.stage_current,
            "stage_total": record.stage_total,
            "stage_progress": record.stage_progress,
            "overall_progress": record.overall_progress,
            "error": record.error,
            "error_diagnostics": record.error_diagnostics,
            "metrics": record.metrics,
            "translation_summary": record.translation_summary,
            "quality_summary": record.quality_summary,
            "failed_paragraphs": record.failed_paragraphs,
            "attempt": record.attempt,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "cancel_requested": record.cancel_requested,
            "progress_detail": record.progress_detail,
            "submission_fingerprint": record.submission_fingerprint,
            "recovery_state": record.recovery_state,
            "timing": record.timing_snapshot(),
            "result_files": {
                output_mode: {
                    "output_mode": output_file.output_mode,
                    "output_path": str(output_file.output_path),
                    "filename": output_file.filename,
                }
                for output_mode, output_file in record.result_files.items()
            },
        }

    @staticmethod
    def _record_from_persistence(payload: dict[str, Any]) -> TaskRecord | None:
        try:
            result_files_payload = payload.get("result_files", {})
            result_files = {}
            if isinstance(result_files_payload, dict):
                for output_mode, output_payload in result_files_payload.items():
                    if not isinstance(output_payload, dict):
                        continue
                    output_path = output_payload.get("output_path")
                    filename = output_payload.get("filename")
                    if not output_path or not filename:
                        continue
                    result_files[str(output_mode)] = TranslationOutputFile(
                        output_mode=str(
                            output_payload.get("output_mode") or output_mode
                        ),
                        output_path=Path(output_path),
                        filename=str(filename),
                    )

            service = str(payload["service"])
            metrics_payload = payload.get("metrics")
            metrics = (
                dict(metrics_payload)
                if isinstance(metrics_payload, dict)
                else (empty_metrics() if supports_request_metrics(service) else None)
            )
            if metrics is not None:
                metrics.pop("cost", None)
                # Before task timers existed, Queue Wait measured the engine lock.
                if payload.get("timing") is None and "Queue Wait" in metrics.get("stageDurations", {}):
                    metrics = copy.deepcopy(metrics)
                    stages = metrics["stageDurations"]
                    stages["Engine Wait"] = stages.pop("Queue Wait")
            return TaskRecord(
                task_id=str(payload["task_id"]),
                file_name=str(payload["file_name"]),
                service=service,
                output_modes=list(payload.get("output_modes", [])),
                request_payload=dict(payload.get("request_payload", {})),
                workspace_dir=Path(payload["workspace_dir"]),
                status=str(payload.get("status", "failed")),
                stage=payload.get("stage"),
                stage_current=TaskManager._coerce_int(payload.get("stage_current"), 0),
                stage_total=TaskManager._coerce_int(payload.get("stage_total"), 0),
                stage_progress=TaskManager._coerce_float(
                    payload.get("stage_progress"),
                    0.0,
                ),
                overall_progress=TaskManager._coerce_float(
                    payload.get("overall_progress"),
                    0.0,
                ),
                error=payload.get("error"),
                error_diagnostics=list(payload.get("error_diagnostics") or []),
                result_files=result_files,
                metrics=metrics,
                translation_summary=payload.get("translation_summary"),
                quality_summary=payload.get("quality_summary"),
                failed_paragraphs=list(payload.get("failed_paragraphs") or []),
                attempt=TaskManager._coerce_int(payload.get("attempt"), 1),
                created_at=str(payload.get("created_at") or utc_now_iso()),
                updated_at=str(payload.get("updated_at") or utc_now_iso()),
                cancel_requested=bool(payload.get("cancel_requested", False)),
                progress_detail=safe_fields(payload.get("progress_detail", {})),
                submission_fingerprint=payload.get("submission_fingerprint"),
                recovery_state=payload.get("recovery_state"),
                timing=payload.get("timing"),
            )
        except (KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def _coerce_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _coerce_float(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
