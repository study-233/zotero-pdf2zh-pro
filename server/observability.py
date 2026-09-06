from __future__ import annotations

import json
import logging
import math
import threading
import time
from collections import deque
from typing import Any, Callable

METRIC_LOGGER = logging.getLogger("zotero_pdf2zh_server.metrics")
MetricsCallback = Callable[[dict[str, Any]], None]

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
        "requests": {
            "attempts": 0,
            "succeeded": 0,
            "failed": 0,
            "active": 0,
            "retries": 0,
            "qps10s": 0.0,
            "averageLatencyMs": None,
            "p95LatencyMs": None,
        },
        "localCache": {"hits": 0, "misses": 0, "hitRate": None},
        "providerCache": {
            "hitTokens": None,
            "missTokens": None,
            "hitRate": None,
            "availability": "unavailable",
        },
        "tokens": {
            "input": None,
            "output": None,
            "total": None,
            "availability": "unavailable",
        },
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
        self._latencies_ms: deque[float] = deque(maxlen=200)
        self._latency_total_ms = 0.0
        self._attempts = 0
        self._succeeded = 0
        self._failed = 0
        self._active = 0
        self._retries = 0
        self._local_hits = 0
        self._local_misses = 0
        self._provider_hit_tokens = 0
        self._provider_miss_tokens = 0
        self._output_tokens = 0
        self._input_tokens = 0
        self._usage_calls = 0
        self._input_known = 0
        self._output_known = 0
        self._cache_known = 0
        self._progress_samples: deque[tuple[float, float]] = deque()
        self._paragraph_samples: deque[tuple[float, int]] = deque()
        self._last_translation_current: int | None = None
        self._references_skipped = 0

    def request_started(self) -> float:
        now = self.clock()
        with self._lock:
            self._attempts += 1
            self._active += 1
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
    ) -> None:
        now = self.clock()
        latency_ms = max((now - started_at) * 1000, 0.0)
        with self._lock:
            self._active = max(self._active - 1, 0)
            if succeeded:
                self._succeeded += 1
            else:
                self._failed += 1
            self._latency_total_ms += latency_ms
            self._latencies_ms.append(latency_ms)
        METRIC_LOGGER.info(
            "metric=%s",
            json.dumps(
                {
                    "taskId": self.task_id,
                    "provider": self.provider,
                    "model": self.model,
                    "event": "request",
                    "success": succeeded,
                    "statusCode": status_code,
                    "latencyMs": round(latency_ms, 1),
                },
                separators=(",", ":"),
            ),
        )
        self._emit_if_due()

    def retry_scheduled(self) -> None:
        with self._lock:
            self._retries += 1
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
    ) -> None:
        with self._lock:
            self._usage_calls += 1
            if prompt_tokens is not None:
                prompt_tokens = max(int(prompt_tokens), 0)
                self._input_tokens += prompt_tokens
                self._input_known += 1
            if completion_tokens is not None:
                self._output_tokens += max(int(completion_tokens), 0)
                self._output_known += 1
            if prompt_tokens is not None and cache_hit_tokens is not None:
                hit = min(max(int(cache_hit_tokens), 0), prompt_tokens)
                miss = prompt_tokens - hit
            elif prompt_tokens is not None and cache_miss_tokens is not None:
                miss = min(max(int(cache_miss_tokens), 0), prompt_tokens)
                hit = prompt_tokens - miss
            elif cache_hit_tokens is not None and cache_miss_tokens is not None:
                hit, miss = (
                    max(int(cache_hit_tokens), 0),
                    max(int(cache_miss_tokens), 0),
                )
            else:
                hit = miss = None
            if hit is not None:
                self._provider_hit_tokens += hit
                self._provider_miss_tokens += miss
                self._cache_known += 1
        self._emit_if_due()

    def _availability(self, *counts: int) -> str:
        if not any(counts):
            return "unavailable"
        return "complete" if all(n == self._usage_calls for n in counts) else "partial"

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
            provider_total = self._provider_hit_tokens + self._provider_miss_tokens
            latency_count = self._succeeded + self._failed
            sorted_latencies = sorted(self._latencies_ms)
            p95 = None
            if sorted_latencies:
                index = max(math.ceil(len(sorted_latencies) * 0.95) - 1, 0)
                p95 = sorted_latencies[index]
            paragraphs = sum(value for _, value in self._paragraph_samples)
            throughput_window = min(max(now - self._started_at, 1.0), 30.0)
            throughput = (
                paragraphs * 60.0 / throughput_window
                if self._paragraph_samples
                else None
            )
            eta = self._eta_locked(now)
            metrics = empty_metrics()
            metrics["requests"] = {
                "attempts": self._attempts,
                "succeeded": self._succeeded,
                "failed": self._failed,
                "active": self._active,
                "retries": self._retries,
                "qps10s": round(len(self._attempt_starts) / 10.0, 2),
                "averageLatencyMs": (
                    round(self._latency_total_ms / latency_count, 1)
                    if latency_count
                    else None
                ),
                "p95LatencyMs": round(p95, 1) if p95 is not None else None,
            }
            metrics["localCache"] = {
                "hits": self._local_hits,
                "misses": self._local_misses,
                "hitRate": (
                    round(self._local_hits / local_total, 4) if local_total else None
                ),
            }
            metrics["providerCache"] = {
                "hitTokens": self._provider_hit_tokens if self._cache_known else None,
                "missTokens": self._provider_miss_tokens if self._cache_known else None,
                "availability": self._availability(self._cache_known),
                "hitRate": (
                    round(self._provider_hit_tokens / provider_total, 4)
                    if provider_total
                    else None
                ),
            }
            metrics["tokens"] = {
                "input": self._input_tokens if self._input_known else None,
                "output": self._output_tokens if self._output_known else None,
                "total": (self._input_tokens + self._output_tokens)
                if self._input_known or self._output_known
                else None,
                "availability": self._availability(
                    self._input_known, self._output_known
                ),
            }
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
