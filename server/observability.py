from __future__ import annotations

import json
import logging
import math
import threading
import time
from collections import Counter, deque
from typing import Any, Callable

METRIC_LOGGER = logging.getLogger("zotero_pdf2zh_server.metrics")
MetricsCallback = Callable[[dict[str, Any]], None]
REQUEST_KINDS = ("translation", "review", "initialization")


def _kind(value):
    return value if value in REQUEST_KINDS else "translation"


def _number(value):
    return (int(value) if isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0 else None)


class _RequestStats:
    def __init__(self):
        self.attempts = self.succeeded = self.failed = self.active = self.retries = 0
        self.latency_total = 0.0
        self.latencies = deque(maxlen=200)
        self.status_codes, self.errors, self.finishes, self.protocols, self.batch_sizes = (Counter() for _ in range(5))
        self.visible_chars = self.visible_known = 0

    def finish(self, *, succeeded, latency_ms, status_code=None, error_type=None,
               finish_reason=None, protocol=None, visible_chars=None, batch_size=None):
        self.succeeded += bool(succeeded)
        self.failed += not succeeded
        self.latency_total += latency_ms
        self.latencies.append(latency_ms)
        self.status_codes[str(status_code) if status_code is not None else "unknown"] += 1
        if error_type:
            self.errors[error_type] += 1
        if finish_reason:
            self.finishes[finish_reason] += 1
        if protocol:
            self.protocols[protocol] += 1
        if visible_chars is not None:
            self.visible_chars += visible_chars
            self.visible_known += 1
        if batch_size is not None:
            self.batch_sizes[str(batch_size)] += 1

    def snapshot(self):
        total = self.succeeded + self.failed
        ordered = sorted(self.latencies)
        return {
            "attempts": self.attempts, "succeeded": self.succeeded, "failed": self.failed,
            "active": self.active, "retries": self.retries,
            "averageLatencyMs": round(self.latency_total / total, 1) if total else None,
            "p95LatencyMs": round(ordered[max(math.ceil(len(ordered) * .95) - 1, 0)], 1) if ordered else None,
            "statusCodes": dict(self.status_codes), "errorTypes": dict(self.errors),
            "finishReasons": dict(self.finishes), "protocols": dict(self.protocols),
            "visibleOutputChars": self.visible_chars if self.visible_known else None,
            "batchSizes": dict(self.batch_sizes),
        }


class _TokenTotals:
    def __init__(self):
        self.calls = 0
        self.values = Counter()
        self.known = Counter()

    def record(self, *, prompt_tokens, completion_tokens, cache_hit_tokens, cache_miss_tokens,
               reasoning_tokens=None):
        prompt, completion, hit, miss, reasoning = map(_number, (
            prompt_tokens, completion_tokens, cache_hit_tokens, cache_miss_tokens, reasoning_tokens))
        if prompt is not None and hit is not None:
            hit = min(hit, prompt)
            miss = prompt - hit
        elif prompt is not None and miss is not None:
            miss = min(miss, prompt)
            hit = prompt - miss
        if reasoning is not None and completion is not None:
            reasoning = min(reasoning, completion)
        self.calls += 1
        for key, value in (("input", prompt), ("output", completion), ("reasoning", reasoning)):
            if value is not None:
                self.values[key] += value
                self.known[key] += 1
        if hit is not None and miss is not None:
            self.values["hit"] += hit
            self.values["miss"] += miss
            self.known["cache"] += 1

    def availability(self, *keys):
        counts = [self.known[key] for key in keys]
        if not any(counts):
            return "unavailable"
        return "complete" if all(count == self.calls for count in counts) else "partial"

    def snapshot(self):
        return {
            "input": self.values["input"] if self.known["input"] else None,
            "output": self.values["output"] if self.known["output"] else None,
            "total": self.values["input"] + self.values["output"]
            if self.known["input"] or self.known["output"] else None,
            "reasoning": self.values["reasoning"] if self.known["reasoning"] else None,
            "availability": self.availability("input", "output"),
            "reasoningAvailability": self.availability("reasoning"),
        }

    def cache_snapshot(self):
        total = self.values["hit"] + self.values["miss"]
        return {
            "hitTokens": self.values["hit"] if self.known["cache"] else None,
            "missTokens": self.values["miss"] if self.known["cache"] else None,
            "hitRate": round(self.values["hit"] / total, 4) if total else None,
            "availability": self.availability("cache"),
        }

# Presets whose settings transform into the common OpenAI translator.
REQUEST_METRIC_SERVICES = frozenset(
    {
        "openai",
        "openaicompatible",
        "deepseek",
        "aliyundashscope",
        "modelscope",
        "zhipu",
        "gemini",
        "grok",
        "groq",
    }
)


def supports_request_metrics(service: str) -> bool:
    return service.lower() in REQUEST_METRIC_SERVICES


def empty_metrics() -> dict[str, Any]:
    return {
        "requests": {**_RequestStats().snapshot(), "qps10s": 0.0,
                     "byKind": {kind: _RequestStats().snapshot() for kind in REQUEST_KINDS}},
        "localCache": {"hits": 0, "misses": 0, "hitRate": None},
        "providerCache": {
            "hitTokens": None,
            "missTokens": None,
            "hitRate": None,
            "availability": "unavailable",
        },
        "tokens": {**_TokenTotals().snapshot(),
                   "byKind": {kind: _TokenTotals().snapshot() for kind in REQUEST_KINDS}},
        "stageDurations": {},
        "throughput": {"paragraphsPerMinute": None, "etaSeconds": None},
        "referencesSkipped": 0,
    }


class TaskMetricsCollector:
    """Thread-safe request and task metric aggregation with throttled snapshots."""

    def __init__(
        self,
        *,
        task_id: str,
        provider: str,
        model: str,
        callback: MetricsCallback | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.task_id = task_id
        self.provider = provider
        self.model = model
        self.callback = callback
        self.clock = clock
        self._lock = threading.RLock()
        self._started_at = clock()
        self._last_emit_at = float("-inf")
        self._attempt_starts: deque[float] = deque()
        self._requests = _RequestStats()
        self._requests_by_kind = {kind: _RequestStats() for kind in REQUEST_KINDS}
        self._local_hits = 0
        self._local_misses = 0
        self._usage = _TokenTotals()
        self._usage_by_kind = {kind: _TokenTotals() for kind in REQUEST_KINDS}
        self._stage_started = {}
        self._stage_durations = Counter()
        self._progress_samples: deque[tuple[float, float]] = deque()
        self._paragraph_samples: deque[tuple[float, int]] = deque()
        self._last_translation_current: int | None = None
        self._references_skipped = 0

    def request_started(self, *, kind="translation") -> float:
        now = self.clock()
        with self._lock:
            for stats in (self._requests, self._requests_by_kind[_kind(kind)]):
                stats.attempts += 1
                stats.active += 1
            self._attempt_starts.append(now)
            self._trim_locked(now)
        self._emit_if_due()
        return now

    def request_finished(
        self,
        started_at: float,
        *,
        succeeded: bool,
        status_code: int | None = None,
        kind="translation", error_type=None, finish_reason=None, protocol=None,
        visible_chars=None, batch_size=None,
    ) -> None:
        now = self.clock()
        latency_ms = max((now - started_at) * 1000, 0.0)
        self._finish_request(kind=kind, latency_ms=latency_ms, succeeded=succeeded,
                             status_code=status_code, error_type=error_type, finish_reason=finish_reason,
                             protocol=protocol, visible_chars=visible_chars, batch_size=batch_size)

    def record_completed_request(self, *, kind, latency_ms, succeeded, status_code=None,
                                 error_type=None, finish_reason=None, protocol=None,
                                 visible_chars=None, batch_size=None):
        """Replay completed initialization without fabricating current active/QPS."""
        with self._lock:
            for stats in (self._requests, self._requests_by_kind[_kind(kind)]):
                stats.attempts += 1
        self._finish_request(kind=kind, latency_ms=latency_ms, succeeded=succeeded,
                             status_code=status_code, error_type=error_type, finish_reason=finish_reason,
                             protocol=protocol, visible_chars=visible_chars, batch_size=batch_size, replay=True)

    def _finish_request(self, *, kind, latency_ms, succeeded, status_code, error_type,
                        finish_reason, protocol, visible_chars, batch_size, replay=False):
        kind = _kind(kind)
        status_code = status_code if type(status_code) is int and 100 <= status_code <= 599 else None
        error_type = error_type if isinstance(error_type, str) and error_type.isidentifier() and len(error_type) <= 80 else None
        finish_reason = (finish_reason if finish_reason in {
            "stop", "length", "content_filter", "tool_calls", "function_call", "completed",
            "incomplete", "failed", "cancelled", "queued", "in_progress", "max_output_tokens",
        } else "other" if finish_reason is not None else None)
        protocol = protocol if protocol in {"chat_completions", "responses"} else None
        visible_chars, batch_size = _number(visible_chars), _number(batch_size)
        latency_ms = max(float(latency_ms), 0.0) if math.isfinite(float(latency_ms)) else 0.0
        with self._lock:
            for stats in (self._requests, self._requests_by_kind[kind]):
                if not replay:
                    stats.active = max(stats.active - 1, 0)
                stats.finish(succeeded=succeeded, latency_ms=latency_ms, status_code=status_code,
                             error_type=error_type, finish_reason=finish_reason, protocol=protocol,
                             visible_chars=visible_chars, batch_size=batch_size)
        METRIC_LOGGER.info(
            "metric=%s",
            json.dumps(
                {
                    "taskId": self.task_id,
                    "provider": self.provider,
                    "model": self.model,
                    "event": "request",
                    "kind": kind,
                    "success": succeeded,
                    "statusCode": status_code,
                    "errorType": error_type, "finishReason": finish_reason,
                    "protocol": protocol, "visibleOutputChars": visible_chars,
                    "batchSize": batch_size,
                    "latencyMs": round(latency_ms, 1),
                },
                separators=(",", ":"),
            ),
        )
        self._emit_if_due()

    def retry_scheduled(self, *, kind="translation") -> None:
        with self._lock:
            self._requests.retries += 1
            self._requests_by_kind[_kind(kind)].retries += 1
        self._emit_if_due()

    def local_cache_hit(self) -> None:
        with self._lock:
            self._local_hits += 1
        self._emit_if_due()

    def local_cache_miss(self) -> None:
        with self._lock:
            self._local_misses += 1
        self._emit_if_due()

    def record_usage(
        self,
        *,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        cache_hit_tokens: int | None,
        cache_miss_tokens: int | None,
        reasoning_tokens: int | None = None,
        kind="translation",
    ) -> None:
        with self._lock:
            for totals in (self._usage, self._usage_by_kind[_kind(kind)]):
                totals.record(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                              cache_hit_tokens=cache_hit_tokens, cache_miss_tokens=cache_miss_tokens,
                              reasoning_tokens=reasoning_tokens)
        self._emit_if_due()

    def record_stage(self, name: str, seconds: float) -> None:
        if not isinstance(name, str) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds < 0:
            return
        with self._lock:
            self._stage_durations[name] += seconds
        self._emit_if_due()

    def update_progress(self, event: dict[str, Any]) -> None:
        if event.get("type") not in {
            "progress_start",
            "progress_update",
            "progress_end",
        }:
            return
        now = self.clock()
        try:
            overall = max(0.0, min(float(event.get("overall_progress") or 0), 100.0))
        except (TypeError, ValueError):
            overall = 0.0
        with self._lock:
            self._progress_samples.append((now, overall))
            stage = str(event.get("stage") or "")
            if stage:
                if event.get("type") == "progress_start" or stage not in self._stage_started:
                    self._stage_started[stage] = now
                completed = event.get("type") == "progress_end"
                if stage in {"Check Fonts", "Download Fonts"}:
                    try:
                        completed = completed or float(event.get("stage_current", -1)) >= float(event["stage_total"])
                    except (KeyError, ValueError, TypeError):
                        pass
                if completed:
                    self._stage_durations[stage] += max(now - self._stage_started.pop(stage), 0.0)
            if stage == "Translate Paragraphs":
                try:
                    current = max(int(event.get("stage_current") or 0), 0)
                except (TypeError, ValueError):
                    current = 0
                if self._last_translation_current is None:
                    self._last_translation_current = current
                elif current > self._last_translation_current:
                    self._paragraph_samples.append(
                        (now, current - self._last_translation_current)
                    )
                    self._last_translation_current = current
            self._trim_locked(now)
        self._emit_if_due()

    def reference_skipped(self, count: int = 1) -> None:
        with self._lock:
            self._references_skipped += max(int(count), 0)
        self._emit_if_due()

    def snapshot(self) -> dict[str, Any]:
        now = self.clock()
        with self._lock:
            self._trim_locked(now)
            local_total = self._local_hits + self._local_misses
            paragraphs = sum(value for _, value in self._paragraph_samples)
            throughput_window = min(max(now - self._started_at, 1.0), 30.0)
            throughput = (
                paragraphs * 60.0 / throughput_window
                if self._paragraph_samples
                else None
            )
            eta = self._eta_locked(now)
            metrics = empty_metrics()
            metrics["requests"] = {**self._requests.snapshot(),
                "byKind": {kind: stats.snapshot() for kind, stats in self._requests_by_kind.items()},
                "qps10s": round(len(self._attempt_starts) / 10.0, 2),
            }
            metrics["localCache"] = {
                "hits": self._local_hits,
                "misses": self._local_misses,
                "hitRate": (
                    round(self._local_hits / local_total, 4) if local_total else None
                ),
            }
            metrics["providerCache"] = self._usage.cache_snapshot()
            metrics["tokens"] = {**self._usage.snapshot(),
                                 "byKind": {kind: totals.snapshot() for kind, totals in self._usage_by_kind.items()}}
            metrics["stageDurations"] = {name: round(seconds, 6) for name, seconds in self._stage_durations.items()}
            metrics["throughput"] = {
                "paragraphsPerMinute": (
                    round(throughput, 1) if throughput is not None else None
                ),
                "etaSeconds": round(eta) if eta is not None else None,
            }
            metrics["referencesSkipped"] = self._references_skipped
            return metrics

    def emit_final(self) -> None:
        self._emit(force=True)

    def emit_heartbeat(self) -> None:
        self._emit(force=False)

    def _eta_locked(self, now: float) -> float | None:
        elapsed = now - self._started_at
        if elapsed < 15 or len(self._progress_samples) < 2:
            return None
        latest_progress = self._progress_samples[-1][1]
        if latest_progress < 2:
            return None
        first_time, first_progress = self._progress_samples[0]
        duration = now - first_time
        delta = latest_progress - first_progress
        if duration <= 0 or delta <= 0:
            return None
        rate = delta / duration
        if rate <= 0:
            return None
        return max((100.0 - latest_progress) / rate, 0.0)

    def _trim_locked(self, now: float) -> None:
        while self._attempt_starts and now - self._attempt_starts[0] > 10:
            self._attempt_starts.popleft()
        while self._progress_samples and now - self._progress_samples[0][0] > 30:
            self._progress_samples.popleft()
        while self._paragraph_samples and now - self._paragraph_samples[0][0] > 30:
            self._paragraph_samples.popleft()

    def _emit_if_due(self) -> None:
        self._emit(force=False)

    def _emit(self, *, force: bool) -> None:
        callback = self.callback
        if callback is None:
            return
        now = self.clock()
        with self._lock:
            if not force and now - self._last_emit_at < 1:
                return
            self._last_emit_at = now
        try:
            callback(self.snapshot())
        except Exception as error:
            METRIC_LOGGER.warning(
                "metric_callback_failed taskId=%s errorType=%s",
                self.task_id,
                type(error).__name__,
            )
