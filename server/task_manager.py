from __future__ import annotations

import asyncio
import copy
import json
import logging
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
from observability import empty_metrics, supports_request_metrics
from diagnostics import DiagnosticStore, safe_fields, requested_configuration
from task_runtime import run_translation, CleanupFailed, recover_leases

TaskStatus = str
LOGGER = logging.getLogger("zotero_pdf2zh_server.tasks")


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
            "canCancel": self.status in {"queued", "running", "cancelling"},
            "cancelRequested": self.cancel_requested,
            "boundedCancellation": True,
        }
        payload.update(self.progress_detail)
        if self.metrics is not None:
            payload["metrics"] = self.metrics
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
        self._subscribers: set[TaskSubscription] = set()
        self._server_instance_id = uuid.uuid4().hex
        self._revision = 0
        self._pending: queue.Queue[tuple[str, int] | None] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._closed = False
        self._persistence_path = (
            Path(persistence_path) if persistence_path is not None else None
        )
        self._snapshot_events = {}
        self._translator = translator
        diagnostic_root = self._persistence_path.parent / "diagnostics" if self._persistence_path else None
        self.diagnostics = DiagnosticStore(diagnostic_root)
        self._queue_blocked = recover_leases(diagnostic_root) if diagnostic_root and diagnostic_root.exists() else False
        self._load_persistent_tasks()
        if self._persistence_path:
            from translation_memory import sync_recovery
            memory_path = self._persistence_path.parent / "translation-memory.sqlite3"
            for record in sorted(self._tasks.values(), key=lambda task: task.updated_at):
                if record.status == "completed" or (record.status == "incomplete" and record.result_files):
                    try:
                        sync_recovery(memory_path, record.request_payload)
                    except Exception:
                        LOGGER.warning("Could not migrate task translation memory")

    def list_tasks(self) -> list[dict[str, Any]]:
        with self._lock:
            records = sorted(
                self._tasks.values(), key=lambda record: record.created_at, reverse=True
            )
            return [record.to_dict() for record in records]

    def sync_metadata(self) -> dict[str, Any]:
        with self._lock:
            return {"serverInstanceId": self._server_instance_id, "revision": self._revision}

    def list_tasks_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {**self.sync_metadata(), "tasks": self.list_tasks()}

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
    ) -> dict[str, Any]:
        record = TaskRecord(
            task_id=task_id,
            file_name=file_name,
            service=service,
            output_modes=output_modes,
            request_payload=request_payload,
            workspace_dir=workspace_dir,
            metrics=empty_metrics() if supports_request_metrics(service) else None,
        )
        with self._lock:
            self._ensure_open_locked()
            self._tasks[task_id] = record
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
        self._save_persistent_tasks()
        with self._lock:
            self._start_worker_locked()
        return snapshot

    def subscribe(self) -> TaskSubscription:
        with self._lock:
            subscription = TaskSubscription(
                initial_events=[
                    {
                        "type": "snapshot", "task": snapshot,
                        "serverInstanceId": self._server_instance_id,
                        "revision": snapshot["revision"],
                    }
                    for snapshot in self.list_tasks()
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
        with self._lock:
            record = self._tasks.get(task_id)
            if record is None:
                return None
            if record.status not in {"queued", "running", "cancelling"}:
                return record.to_dict()
            record.progress_detail.setdefault("cancelPhase", "requested")
            if not record.cancel_event.is_set():
                record.cancel_event.requested_at = time.monotonic()
            record.cancel_requested = True
            record.cancel_event.set()
            record.status = "cancelled" if record.status == "queued" else "cancelling"
            if record.status == "cancelled":
                record.stage = "cancelled"
                record.progress_detail.update(cancelPhase="cancelled", cancelReason="user")
                record.error = None
                record.error_diagnostics = []
            snapshot = self._task_changed_locked(record)
            cancel_callback = record.cancel_callback

        LOGGER.info("[%s] cancellation requested", task_id)
        if cancel_callback is not None:
            cancel_callback()
        self._save_persistent_tasks()
        return snapshot

    def delete_task(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._tasks.get(task_id)
            if record is None:
                return None
            if record.status in {"queued", "running", "cancelling"}:
                raise ValueError("Active task cannot be deleted")
            deleted = self._tasks.pop(task_id)
            self._deleted_locked(deleted)
            snapshot = deleted.to_dict()

        shutil.rmtree(deleted.workspace_dir, ignore_errors=True)
        self._save_persistent_tasks()
        LOGGER.info("[%s] task deleted", task_id)
        return snapshot

    def clear_failed_tasks(self) -> int:
        with self._lock:
            failed_task_ids = [
                task_id
                for task_id, record in self._tasks.items()
                if record.status == "failed"
            ]
            deleted_records = [self._tasks.pop(task_id) for task_id in failed_task_ids]
            for record in deleted_records:
                self._deleted_locked(record)

        for record in deleted_records:
            shutil.rmtree(record.workspace_dir, ignore_errors=True)

        self._save_persistent_tasks()
        if failed_task_ids:
            LOGGER.info("cleared failed tasks: %s", ",".join(failed_task_ids))
        return len(failed_task_ids)

    def repair_task(self, task_id: str) -> dict[str, Any] | None:
        return self.retry_task(task_id, repair=True)

    def retry_task(self, task_id: str, *, repair=False) -> dict[str, Any] | None:
        with self._lock:
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
            output_dir = Path(record.request_payload["output_dir"])
            if not input_path.exists():
                raise ValueError("Task input file is no longer available")

            if repair:
                record.request_payload.update(qps=2, pool_size=4, repair_attempt=record.attempt + 1)
                output_dir = record.workspace_dir / f"repair-{record.attempt + 1}"
                record.request_payload["output_dir"] = str(output_dir)
            else:
                shutil.rmtree(output_dir, ignore_errors=True)
            output_dir.mkdir(parents=True, exist_ok=True)

            record.status = "queued"
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
            snapshot = self._task_changed_locked(record)
            self._pending.put_nowait((task_id, record.attempt))

        self._save_persistent_tasks()
        LOGGER.info("[%s] task retry queued", task_id)
        with self._lock:
            self._start_worker_locked()
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
                for record in self._tasks.values():
                    if record.status not in {"queued", "running", "cancelling"}:
                        continue
                    changed = True
                    if not record.cancel_event.is_set():
                        record.cancel_event.requested_at = time.monotonic()
                    record.cancel_event.set()
                    if record.status == "queued":
                        self._finish_cancellation_locked(record)
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

    def _run_task(self, task_id: str, attempt: int | None = None) -> None:
        with self._lock:
            record = self._tasks.get(task_id)
            if record is None or (
                attempt is not None
                and (record.attempt != attempt or record.status != "queued")
            ):
                return
            attempt = record.attempt
            record.progress_detail = {"lastProgressAt": utc_now_iso(), "operation": "initialization", "idleSeconds": 0, **record.progress_detail}
            record.status = "cancelling" if record.cancel_requested else "running"
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
        metrics = lambda value: self._handle_metrics_event(task_id, value, attempt=attempt)
        try:
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
            record.quality_summary = getattr(result, "quality_summary", record.quality_summary)
            record.failed_paragraphs = result.failed_paragraphs
            incomplete = bool(result.translation_summary and (result.translation_summary.get("failed", 0) or result.translation_summary.get("pending", 0)))
            cancelled = record.cancel_event.is_set()
            record.result_files = {} if cancelled else dict(result.files)
            if cancelled:
                self._finish_cancellation_locked(record)
            else:
                record.status = "incomplete" if incomplete else "completed"
                record.stage = record.status
                record.error = f"未完成，剩余 {result.translation_summary.get('failed', 0) + result.translation_summary.get('pending', 0)} 段，可补译" if incomplete else None
                record.stage_progress = 99.0 if incomplete else 100.0
                record.overall_progress = 99.0 if incomplete else 100.0
            record.cancel_callback = None
            self._task_changed_locked(record)
        self._save_persistent_tasks()
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
    ) -> None:
        with self._lock:
            record = self._tasks.get(task_id)
            if record is None or (attempt is not None and record.attempt != attempt):
                return
            if record.status not in {"queued", "running", "cancelling"}:
                return
            if metrics.get("requests") and metrics.get("requests") != (record.metrics or {}).get("requests"):
                record.progress_detail["lastRequestAt"] = utc_now_iso()
            record.metrics = copy.deepcopy(metrics)
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
        record.progress_detail["cancelPhase"] = "cancelled"
        if interrupted:
            record.progress_detail["cancelReason"] = "shutdown"
        record.stage = record.status
        record.error = "服务关闭中断了翻译，可补译继续" if interrupted else None
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
        if self._persistence_path is None or not self._persistence_path.exists():
            return

        try:
            payload = json.loads(self._persistence_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.warning("failed to load persisted tasks: %s", exc)
            return

        records = payload.get("tasks", []) if isinstance(payload, dict) else []
        if not isinstance(records, list):
            return

        for record_payload in records:
            if not isinstance(record_payload, dict):
                continue
            record = self._record_from_persistence(record_payload)
            if record is None:
                continue
            if record.status in {"queued", "running", "cancelling"}:
                record.status = "incomplete"
                record.stage = "incomplete"
                record.overall_progress = min(99.0, record.overall_progress)
                record.error = "服务重启中断了翻译，可补译继续"
                record.cancel_requested = False
            self._revision += 1
            record.server_instance_id = self._server_instance_id
            record.revision = self._revision
            self._tasks[record.task_id] = record

    def _save_persistent_tasks(self) -> None:
        with self._persistence_lock:
            self._save_persistent_tasks_locked()

    def _save_persistent_tasks_locked(self) -> None:
        if self._persistence_path is None:
            return

        with self._lock:
            records = copy.deepcopy([
                self._record_to_persistence(record)
                for record in self._tasks.values()
            ])

        payload = {"tasks": records}
        try:
            self._persistence_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self._persistence_path.with_suffix(
                self._persistence_path.suffix + ".tmp"
            )
            temp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temp_path.replace(self._persistence_path)
        except OSError as exc:
            LOGGER.warning("failed to save persisted tasks: %s", exc)

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
