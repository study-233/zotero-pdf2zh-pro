from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from typing import Callable
from urllib.request import Request
from urllib.request import urlopen


METRIC_LOGGER = logging.getLogger("zotero_pdf2zh_server.metrics")
MetricsCallback = Callable[[dict[str, Any]], None]
DEEPSEEK_PRICING_LOGGER = logging.getLogger(
    "zotero_pdf2zh_server.deepseek_pricing"
)
DEEPSEEK_PRICING_SOURCE_URL = (
    "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/"
)
DEEPSEEK_PRICING_URL = (
    "https://raw.githubusercontent.com/study-233/zotero-pdf2zh-pro/"
    "main/server/pdf2zh_next/deepseek_pricing.json"
)
DEEPSEEK_PRICING_REFRESH_SECONDS = 24 * 60 * 60
DEEPSEEK_PRICING_TIMEOUT_SECONDS = 5
DEEPSEEK_PRICING_MAX_BYTES = 64 * 1024
REQUIRED_DEEPSEEK_PRICING_NAMES = {
    "deepseek-chat",
    "deepseek-reasoner",
    "deepseek-v4-flash",
    "deepseek-v4-pro",
    "deepseek-v4-flash-vision-exp",
}


@dataclass(frozen=True)
class DeepSeekPricing:
    cache_hit_input: float
    cache_miss_input: float
    output: float
    currency: str
    version: str
    source: str = "custom"
    updated_at: str | None = None


@dataclass(frozen=True)
class DeepSeekTieredPricing:
    off_peak: DeepSeekPricing
    peak: DeepSeekPricing
    utc_offset_minutes: int
    peak_weekdays: tuple[int, ...]
    peak_windows: tuple[tuple[int, int], ...]

    @property
    def currency(self) -> str:
        return self.off_peak.currency

    @property
    def version(self) -> str:
        return self.off_peak.version

    @property
    def source(self) -> str:
        return self.off_peak.source

    @property
    def updated_at(self) -> str | None:
        return self.off_peak.updated_at

    def at(self, moment: datetime) -> DeepSeekPricing:
        local_time = moment.astimezone(
            timezone(timedelta(minutes=self.utc_offset_minutes))
        )
        local_minute = local_time.hour * 60 + local_time.minute
        if local_time.isoweekday() in self.peak_weekdays and any(
            start <= local_minute < end for start, end in self.peak_windows
        ):
            return self.peak
        return self.off_peak


DeepSeekPricingPolicy = DeepSeekPricing | DeepSeekTieredPricing


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


def _pricing_time(value: Any) -> int:
    if not isinstance(value, str):
        raise ValueError("pricing time must be HH:MM")
    parts = value.split(":")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise ValueError("pricing time must be HH:MM")
    hour, minute = (int(part) for part in parts)
    if not 0 <= hour <= 24 or not 0 <= minute < 60:
        raise ValueError("pricing time is out of range")
    if hour == 24 and minute != 0:
        raise ValueError("pricing time is out of range")
    return hour * 60 + minute


def _pricing_rates(
    value: Any,
    *,
    currency: str,
    version: str,
    source: str,
    updated_at: str,
) -> DeepSeekPricing:
    if not isinstance(value, dict):
        raise ValueError("pricing rates must be an object")
    rates = tuple(
        _number(value.get(key))
        for key in ("cacheHitInput", "cacheMissInput", "output")
    )
    if any(rate is None or rate > 10_000 for rate in rates):
        raise ValueError(
            "pricing rates must be finite non-negative reasonable numbers"
        )
    return DeepSeekPricing(
        rates[0], rates[1], rates[2], currency, version, source, updated_at
    )


def parse_deepseek_pricing_manifest(
    payload: bytes | str | dict[str, Any],
    *,
    source: str,
) -> dict[str, DeepSeekPricingPolicy]:
    if isinstance(payload, bytes):
        if len(payload) > DEEPSEEK_PRICING_MAX_BYTES:
            raise ValueError("pricing manifest is too large")
        data = json.loads(payload.decode("utf-8"))
    elif isinstance(payload, str):
        if len(payload.encode("utf-8")) > DEEPSEEK_PRICING_MAX_BYTES:
            raise ValueError("pricing manifest is too large")
        data = json.loads(payload)
    else:
        data = payload
    if not isinstance(data, dict) or data.get("schemaVersion") != 1:
        raise ValueError("unsupported pricing manifest schema")
    version = str(data.get("version") or "").strip()
    updated_at = str(data.get("updatedAt") or "").strip()
    currency = str(data.get("currency") or "").strip().upper()
    source_url = str(data.get("sourceUrl") or "").strip()
    if not version or not updated_at or not currency or not source_url:
        raise ValueError("pricing manifest metadata is incomplete")
    if source_url != DEEPSEEK_PRICING_SOURCE_URL:
        raise ValueError("pricing manifest source is not the official page")
    if data.get("unit") != "per_million_tokens":
        raise ValueError("unsupported pricing unit")
    try:
        parsed_updated_at = datetime.fromisoformat(updated_at)
    except ValueError as error:
        raise ValueError("pricing updatedAt must be ISO 8601") from error
    if parsed_updated_at.utcoffset() is None:
        raise ValueError("pricing updatedAt must include a UTC offset")

    schedule = data.get("schedule")
    if not isinstance(schedule, dict):
        raise ValueError("pricing schedule is missing")
    if schedule.get("timezone") != "Asia/Shanghai":
        raise ValueError("pricing timezone must be Asia/Shanghai")
    offset = schedule.get("utcOffsetMinutes")
    weekdays = schedule.get("peakWeekdays")
    raw_windows = schedule.get("peakWindows")
    if offset != 480:
        raise ValueError("pricing UTC offset must be 480 minutes")
    if (
        not isinstance(weekdays, list)
        or not weekdays
        or any(type(day) is not int or not 1 <= day <= 7 for day in weekdays)
        or len(set(weekdays)) != len(weekdays)
    ):
        raise ValueError("pricing peak weekdays are invalid")
    if not isinstance(raw_windows, list) or not raw_windows:
        raise ValueError("pricing peak windows are invalid")
    windows: list[tuple[int, int]] = []
    for window in raw_windows:
        if not isinstance(window, list) or len(window) != 2:
            raise ValueError("pricing peak window must have start and end")
        start, end = (_pricing_time(value) for value in window)
        if start >= end:
            raise ValueError("pricing peak window start must precede end")
        windows.append((start, end))
    if any(current[0] < previous[1] for previous, current in zip(windows, windows[1:])):
        raise ValueError("pricing peak windows must not overlap")

    models = data.get("models")
    if not isinstance(models, dict) or not models:
        raise ValueError("pricing models are missing")
    policies: dict[str, DeepSeekPricingPolicy] = {}
    for raw_model, model_data in models.items():
        model = str(raw_model).strip().lower()
        if not model or not isinstance(model_data, dict):
            raise ValueError("pricing model entry is invalid")
        aliases = model_data.get("aliases")
        if not isinstance(aliases, list) or any(
            not isinstance(alias, str) or not alias.strip() for alias in aliases
        ):
            raise ValueError("pricing model aliases are invalid")
        off_peak = _pricing_rates(
            model_data.get("offPeak"),
            currency=currency,
            version=version,
            source=source,
            updated_at=updated_at,
        )
        peak = _pricing_rates(
            model_data.get("peak"),
            currency=currency,
            version=version,
            source=source,
            updated_at=updated_at,
        )
        policy = DeepSeekTieredPricing(
            off_peak,
            peak,
            offset,
            tuple(weekdays),
            tuple(windows),
        )
        for name in (model, *(alias.strip().lower() for alias in aliases)):
            if name in policies:
                raise ValueError(f"duplicate pricing model or alias: {name}")
            policies[name] = policy
    missing_names = REQUIRED_DEEPSEEK_PRICING_NAMES - policies.keys()
    if missing_names:
        raise ValueError(
            f"pricing manifest is missing required models: {sorted(missing_names)}"
        )
    return policies


def _bundled_deepseek_pricing() -> dict[str, DeepSeekPricingPolicy]:
    manifest = Path(__file__).with_name("pdf2zh_next") / "deepseek_pricing.json"
    return parse_deepseek_pricing_manifest(
        manifest.read_bytes(),
        source="bundled",
    )


BUILTIN_DEEPSEEK_PRICING = _bundled_deepseek_pricing()


class DeepSeekPricingManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._policies = BUILTIN_DEEPSEEK_PRICING
        self._thread: threading.Thread | None = None

    def resolve(self, model: str) -> DeepSeekPricingPolicy | None:
        with self._lock:
            return self._policies.get(model.strip().lower())

    def load_cache(self, data_dir: str | Path) -> bool:
        cache_path = self._cache_path(data_dir)
        try:
            policies = parse_deepseek_pricing_manifest(
                cache_path.read_bytes(),
                source="remote",
            )
        except FileNotFoundError:
            return False
        except Exception as error:
            DEEPSEEK_PRICING_LOGGER.warning(
                "cached pricing manifest rejected: %s", error
            )
            return False
        with self._lock:
            if self._updated_at(policies) < self._updated_at(self._policies):
                DEEPSEEK_PRICING_LOGGER.warning(
                    "cached pricing manifest is older than the active rules"
                )
                return False
            self._policies = policies
        return True

    def refresh_once(
        self,
        data_dir: str | Path,
        fetcher: Callable[[], bytes] | None = None,
    ) -> str:
        raw = (fetcher or _download_deepseek_pricing_manifest)()
        policies = parse_deepseek_pricing_manifest(raw, source="remote")
        with self._lock:
            if self._updated_at(policies) < self._updated_at(self._policies):
                raise ValueError("remote pricing manifest is older than active rules")
        cache_path = self._cache_path(data_dir)
        previous = cache_path.read_bytes() if cache_path.exists() else None
        if previous != raw:
            self._write_cache(cache_path, raw)
            result = "updated"
        else:
            result = "unchanged"
        with self._lock:
            self._policies = policies
        return result

    def start(self, data_dir: str | Path) -> None:
        self.load_cache(data_dir)
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return

            def update_loop() -> None:
                while True:
                    try:
                        result = self.refresh_once(data_dir)
                        DEEPSEEK_PRICING_LOGGER.info(
                            "DeepSeek pricing refresh %s", result
                        )
                    except Exception as error:
                        DEEPSEEK_PRICING_LOGGER.warning(
                            "DeepSeek pricing refresh failed: %s", error
                        )
                    time.sleep(DEEPSEEK_PRICING_REFRESH_SECONDS)

            self._thread = threading.Thread(
                target=update_loop,
                name="deepseek-pricing-updater",
                daemon=True,
            )
            self._thread.start()

    @staticmethod
    def _cache_path(data_dir: str | Path) -> Path:
        return Path(data_dir) / "pricing" / "deepseek.json"

    @staticmethod
    def _updated_at(policies: dict[str, DeepSeekPricingPolicy]) -> datetime:
        policy = next(iter(policies.values()))
        updated_at = policy.updated_at
        if updated_at is None:
            raise ValueError("pricing catalog has no update timestamp")
        return datetime.fromisoformat(updated_at)

    @staticmethod
    def _write_cache(cache_path: Path, payload: bytes) -> None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(
                mode="wb",
                dir=cache_path.parent,
                prefix=".deepseek-pricing-",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary.write(payload)
                temporary_path = Path(temporary.name)
            os.replace(temporary_path, cache_path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()


def _download_deepseek_pricing_manifest() -> bytes:
    request = Request(
        DEEPSEEK_PRICING_URL,
        headers={"User-Agent": "zotero-pdf2zh-pro/deepseek-pricing"},
    )
    with urlopen(request, timeout=DEEPSEEK_PRICING_TIMEOUT_SECONDS) as response:
        if response.status != 200:
            raise RuntimeError(f"pricing endpoint returned HTTP {response.status}")
        payload = response.read(DEEPSEEK_PRICING_MAX_BYTES + 1)
    if len(payload) > DEEPSEEK_PRICING_MAX_BYTES:
        raise ValueError("pricing manifest is too large")
    return payload


DEEPSEEK_PRICING_MANAGER = DeepSeekPricingManager()


def start_deepseek_pricing_updater(data_dir: str | Path) -> None:
    DEEPSEEK_PRICING_MANAGER.start(data_dir)


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
    return DEEPSEEK_PRICING_MANAGER.resolve(model)


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
            "pricingSource": None,
            "pricingUpdatedAt": None,
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
                "pricingSource": None,
                "pricingUpdatedAt": None,
                "accuracy": "unavailable",
            }
        return {
            "amount": round(self._cost_amount, 6),
            "currency": self.pricing.currency,
            "pricingVersion": self.pricing.version,
            "pricingSource": self.pricing.source,
            "pricingUpdatedAt": self.pricing.updated_at,
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
