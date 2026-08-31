from __future__ import annotations

import json
import logging
import math
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from typing import Any
from typing import Callable


METRIC_LOGGER = logging.getLogger("zotero_pdf2zh_server.metrics")
MetricsCallback = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class DeepSeekPricing:
    cache_hit_input: float
    cache_miss_input: float
    output: float
    currency: str
    version: str


@dataclass(frozen=True)
class DeepSeekTieredPricing:
    off_peak: DeepSeekPricing
    peak: DeepSeekPricing

    @property
    def currency(self) -> str:
        return self.off_peak.currency

    @property
    def version(self) -> str:
        return self.off_peak.version

    def at(self, moment: datetime) -> DeepSeekPricing:
        utc_hour = moment.astimezone(timezone.utc).hour
        if 1 <= utc_hour < 4 or 6 <= utc_hour < 10:
            return self.peak
        return self.off_peak


DeepSeekPricingPolicy = DeepSeekPricing | DeepSeekTieredPricing
DEEPSEEK_TIERED_PRICING_VERSION = "2026-08-17-cny-tiered"


def _tiered_pricing(
    off_peak: tuple[float, float, float],
    peak: tuple[float, float, float],
) -> DeepSeekTieredPricing:
    return DeepSeekTieredPricing(
        DeepSeekPricing(*off_peak, "CNY", DEEPSEEK_TIERED_PRICING_VERSION),
        DeepSeekPricing(*peak, "CNY", DEEPSEEK_TIERED_PRICING_VERSION),
    )


DEEPSEEK_V4_FLASH_PRICING = _tiered_pricing(
    (0.05, 1.5, 4.5),
    (0.1, 3.0, 9.0),
)
BUILTIN_DEEPSEEK_PRICING: dict[str, DeepSeekPricingPolicy] = {
    "deepseek-chat": DEEPSEEK_V4_FLASH_PRICING,
    "deepseek-reasoner": DEEPSEEK_V4_FLASH_PRICING,
    "deepseek-v4-flash": DEEPSEEK_V4_FLASH_PRICING,
    "deepseek-v4-pro": _tiered_pricing(
        (0.15, 4.5, 13.5),
        (0.3, 9.0, 27.0),
    ),
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result) or result < 0:
        return None
    return result


def resolve_deepseek_pricing(
    model: str,
    llm_api: dict[str, Any] | None,
) -> DeepSeekPricingPolicy | None:
    extra_data = (llm_api or {}).get("extraData") or {}
    if isinstance(extra_data, dict):
        hit = _number(extra_data.get("deepseek_cache_hit_input_price"))
        miss = _number(extra_data.get("deepseek_cache_miss_input_price"))
        output = _number(extra_data.get("deepseek_output_price"))
        if hit is not None and miss is not None and output is not None:
            currency = str(
                extra_data.get("deepseek_price_currency") or "CNY"
            ).strip().upper()
            version = str(
                extra_data.get("deepseek_pricing_version") or "custom"
            ).strip()
            return DeepSeekPricing(hit, miss, output, currency, version)
    return BUILTIN_DEEPSEEK_PRICING.get(model.strip().lower())


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
        "providerCache": {"hitTokens": 0, "missTokens": 0, "hitRate": None},
        "tokens": {"input": 0, "output": 0, "total": 0},
        "throughput": {"paragraphsPerMinute": None, "etaSeconds": None},
        "cost": {
            "amount": None,
            "currency": "CNY",
            "pricingVersion": None,
            "accuracy": "unavailable",
        },
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
        pricing: DeepSeekPricingPolicy | None,
        callback: MetricsCallback | None = None,
        clock: Callable[[], float] = time.monotonic,
        utc_now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.task_id = task_id
        self.provider = provider
        self.model = model
        self.pricing = pricing
        self.callback = callback
        self.clock = clock
        self.utc_now = utc_now
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
        self._cost_amount = 0.0
        self._usage_fallback = False
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
        prompt_tokens: int,
        completion_tokens: int,
        cache_hit_tokens: int | None,
        cache_miss_tokens: int | None,
    ) -> None:
        prompt_tokens = max(int(prompt_tokens or 0), 0)
        completion_tokens = max(int(completion_tokens or 0), 0)
        with self._lock:
            if cache_hit_tokens is None or cache_miss_tokens is None:
                hit = 0
                miss = prompt_tokens
                self._usage_fallback = True
            else:
                hit = max(int(cache_hit_tokens or 0), 0)
                miss = max(int(cache_miss_tokens or 0), 0)
            self._provider_hit_tokens += hit
            self._provider_miss_tokens += miss
            self._output_tokens += completion_tokens
            if self.pricing is not None:
                pricing = (
                    self.pricing.at(self.utc_now())
                    if isinstance(self.pricing, DeepSeekTieredPricing)
                    else self.pricing
                )
                self._cost_amount += (
                    hit * pricing.cache_hit_input
                    + miss * pricing.cache_miss_input
                    + completion_tokens * pricing.output
                ) / 1_000_000
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
            provider_total = (
                self._provider_hit_tokens + self._provider_miss_tokens
            )
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
                    round(self._local_hits / local_total, 4)
                    if local_total
                    else None
                ),
            }
            metrics["providerCache"] = {
                "hitTokens": self._provider_hit_tokens,
                "missTokens": self._provider_miss_tokens,
                "hitRate": (
                    round(self._provider_hit_tokens / provider_total, 4)
                    if provider_total
                    else None
                ),
            }
            metrics["tokens"] = {
                "input": provider_total,
                "output": self._output_tokens,
                "total": provider_total + self._output_tokens,
            }
            metrics["throughput"] = {
                "paragraphsPerMinute": (
                    round(throughput, 1) if throughput is not None else None
                ),
                "etaSeconds": round(eta) if eta is not None else None,
            }
            metrics["cost"] = self._cost_locked()
            metrics["referencesSkipped"] = self._references_skipped
            return metrics

    def emit_final(self) -> None:
        self._emit(force=True)

    def emit_heartbeat(self) -> None:
        self._emit(force=False)

    def _cost_locked(self) -> dict[str, Any]:
        if self.pricing is None:
            return {
                "amount": None,
                "currency": "CNY",
                "pricingVersion": None,
                "accuracy": "unavailable",
            }
        return {
            "amount": round(self._cost_amount, 6),
            "currency": self.pricing.currency,
            "pricingVersion": self.pricing.version,
            "accuracy": "fallback" if self._usage_fallback else "exact-tokens",
        }

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
