from __future__ import annotations

import json
import threading
import unittest
from datetime import datetime
from datetime import timezone

from observability import DeepSeekPricing
from observability import TaskMetricsCollector
from observability import resolve_deepseek_pricing


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeUtcClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class ObservabilityTests(unittest.TestCase):
    def make_collector(self, clock: FakeClock | None = None):
        return TaskMetricsCollector(
            task_id="task-1",
            provider="deepseek",
            model="deepseek-chat",
            pricing=DeepSeekPricing(0.5, 2.0, 8.0, "CNY", "test"),
            clock=clock or FakeClock(),
        )

    def test_request_qps_latency_retries_and_active(self) -> None:
        clock = FakeClock()
        collector = self.make_collector(clock)
        started = collector.request_started()
        self.assertEqual(collector.snapshot()["requests"]["active"], 1)
        clock.advance(0.25)
        collector.request_finished(started, succeeded=False, status_code=429)
        collector.retry_scheduled()
        started = collector.request_started()
        clock.advance(0.1)
        collector.request_finished(started, succeeded=True)

        requests = collector.snapshot()["requests"]
        self.assertEqual(requests["attempts"], 2)
        self.assertEqual(requests["succeeded"], 1)
        self.assertEqual(requests["failed"], 1)
        self.assertEqual(requests["retries"], 1)
        self.assertEqual(requests["active"], 0)
        self.assertEqual(requests["qps10s"], 0.2)
        self.assertEqual(requests["averageLatencyMs"], 175.0)
        self.assertEqual(requests["p95LatencyMs"], 250.0)

    def test_cache_tokens_and_exact_cost_are_separate(self) -> None:
        collector = self.make_collector()
        collector.local_cache_hit()
        collector.local_cache_hit()
        collector.local_cache_miss()
        collector.record_usage(
            prompt_tokens=300,
            completion_tokens=50,
            cache_hit_tokens=200,
            cache_miss_tokens=100,
        )
        metrics = collector.snapshot()

        self.assertEqual(metrics["localCache"]["hits"], 2)
        self.assertAlmostEqual(metrics["localCache"]["hitRate"], 2 / 3, places=4)
        self.assertEqual(metrics["providerCache"]["hitTokens"], 200)
        self.assertAlmostEqual(
            metrics["cost"]["amount"],
            (200 * 0.5 + 100 * 2 + 50 * 8) / 1_000_000,
        )
        self.assertEqual(metrics["cost"]["accuracy"], "exact-tokens")

    def test_missing_provider_cache_split_uses_conservative_fallback(self) -> None:
        collector = self.make_collector()
        collector.record_usage(
            prompt_tokens=300,
            completion_tokens=0,
            cache_hit_tokens=None,
            cache_miss_tokens=None,
        )
        metrics = collector.snapshot()
        self.assertEqual(metrics["providerCache"]["missTokens"], 300)
        self.assertEqual(metrics["cost"]["accuracy"], "fallback")

    def test_custom_pricing_overrides_builtin_and_unknown_is_unavailable(self) -> None:
        pricing = resolve_deepseek_pricing(
            "deepseek-v4-flash",
            {
                "extraData": {
                    "deepseek_cache_hit_input_price": "1",
                    "deepseek_cache_miss_input_price": "2",
                    "deepseek_output_price": "3",
                    "deepseek_price_currency": "usd",
                    "deepseek_pricing_version": "custom-v1",
                }
            },
        )
        self.assertEqual(pricing, DeepSeekPricing(1, 2, 3, "USD", "custom-v1"))
        self.assertIsNone(resolve_deepseek_pricing("unknown-model", {}))

    def test_builtin_pricing_switches_at_utc_boundaries(self) -> None:
        cases = (
            ((0, 59), 6.05),
            ((1, 0), 12.1),
            ((4, 0), 6.05),
            ((6, 0), 12.1),
            ((10, 0), 6.05),
        )
        for (hour, minute), expected in cases:
            with self.subTest(hour=hour, minute=minute):
                utc_now = FakeUtcClock(
                    datetime(2026, 8, 17, hour, minute, tzinfo=timezone.utc)
                )
                collector = TaskMetricsCollector(
                    task_id="task-tier-boundary",
                    provider="deepseek",
                    model="deepseek-v4-flash",
                    pricing=resolve_deepseek_pricing("deepseek-v4-flash", {}),
                    utc_now=utc_now,
                )
                collector.record_usage(
                    prompt_tokens=2_000_000,
                    completion_tokens=1_000_000,
                    cache_hit_tokens=1_000_000,
                    cache_miss_tokens=1_000_000,
                )
                self.assertEqual(collector.snapshot()["cost"]["amount"], expected)

    def test_builtin_model_rates_and_legacy_aliases(self) -> None:
        cases = (
            ("deepseek-v4-flash", 6.05),
            ("deepseek-chat", 6.05),
            ("deepseek-reasoner", 6.05),
            ("deepseek-v4-pro", 18.15),
        )
        for model, expected in cases:
            with self.subTest(model=model):
                collector = TaskMetricsCollector(
                    task_id="task-model-rate",
                    provider="deepseek",
                    model=model,
                    pricing=resolve_deepseek_pricing(model, {}),
                    utc_now=lambda: datetime(
                        2026, 8, 17, 0, 0, tzinfo=timezone.utc
                    ),
                )
                collector.record_usage(
                    prompt_tokens=2_000_000,
                    completion_tokens=1_000_000,
                    cache_hit_tokens=1_000_000,
                    cache_miss_tokens=1_000_000,
                )
                cost = collector.snapshot()["cost"]
                self.assertEqual(cost["amount"], expected)
                self.assertEqual(cost["currency"], "CNY")
                self.assertEqual(
                    cost["pricingVersion"], "2026-08-17-cny-tiered"
                )

    def test_cost_accumulates_across_pricing_tiers(self) -> None:
        utc_now = FakeUtcClock(
            datetime(2026, 8, 17, 0, 59, tzinfo=timezone.utc)
        )
        collector = TaskMetricsCollector(
            task_id="task-cross-tier",
            provider="deepseek",
            model="deepseek-v4-flash",
            pricing=resolve_deepseek_pricing("deepseek-v4-flash", {}),
            utc_now=utc_now,
        )
        collector.record_usage(
            prompt_tokens=2_000_000,
            completion_tokens=1_000_000,
            cache_hit_tokens=1_000_000,
            cache_miss_tokens=1_000_000,
        )
        utc_now.value = datetime(2026, 8, 17, 1, 0, tzinfo=timezone.utc)
        collector.record_usage(
            prompt_tokens=2_000_000,
            completion_tokens=1_000_000,
            cache_hit_tokens=1_000_000,
            cache_miss_tokens=1_000_000,
        )
        self.assertEqual(collector.snapshot()["cost"]["amount"], 18.15)

    def test_fallback_uses_current_tier_and_custom_pricing_stays_fixed(self) -> None:
        peak = lambda: datetime(2026, 8, 17, 1, 0, tzinfo=timezone.utc)
        builtin = TaskMetricsCollector(
            task_id="task-fallback",
            provider="deepseek",
            model="deepseek-v4-flash",
            pricing=resolve_deepseek_pricing("deepseek-v4-flash", {}),
            utc_now=peak,
        )
        builtin.record_usage(
            prompt_tokens=1_000_000,
            completion_tokens=0,
            cache_hit_tokens=None,
            cache_miss_tokens=None,
        )
        self.assertEqual(builtin.snapshot()["cost"]["amount"], 3.0)
        self.assertEqual(builtin.snapshot()["cost"]["accuracy"], "fallback")

        custom = TaskMetricsCollector(
            task_id="task-custom",
            provider="deepseek",
            model="deepseek-v4-flash",
            pricing=DeepSeekPricing(1, 2, 3, "CNY", "custom-v1"),
            utc_now=peak,
        )
        custom.record_usage(
            prompt_tokens=2_000_000,
            completion_tokens=1_000_000,
            cache_hit_tokens=1_000_000,
            cache_miss_tokens=1_000_000,
        )
        self.assertEqual(custom.snapshot()["cost"]["amount"], 6.0)

    def test_throughput_and_eta_use_recent_progress(self) -> None:
        clock = FakeClock()
        collector = self.make_collector(clock)
        collector.update_progress(
            {
                "type": "progress_start",
                "stage": "Translate Paragraphs",
                "stage_current": 0,
                "overall_progress": 10,
            }
        )
        clock.advance(15)
        collector.update_progress(
            {
                "type": "progress_update",
                "stage": "Translate Paragraphs",
                "stage_current": 30,
                "overall_progress": 40,
            }
        )
        metrics = collector.snapshot()
        self.assertEqual(metrics["throughput"]["paragraphsPerMinute"], 120.0)
        self.assertEqual(metrics["throughput"]["etaSeconds"], 30)

    def test_concurrent_cache_updates_are_thread_safe(self) -> None:
        collector = self.make_collector()

        def update() -> None:
            for _ in range(100):
                collector.local_cache_hit()

        threads = [threading.Thread(target=update) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(collector.snapshot()["localCache"]["hits"], 400)

    def test_metric_log_payload_has_no_body_or_api_key_fields(self) -> None:
        collector = self.make_collector()
        with self.assertLogs("zotero_pdf2zh_server.metrics", level="INFO") as logs:
            started = collector.request_started()
            collector.request_finished(started, succeeded=True)
        payload = "\n".join(logs.output)
        self.assertNotIn("prompt", payload.lower())
        self.assertNotIn("api_key", payload.lower())
        self.assertNotIn("authorization", payload.lower())
        json.loads(payload.split("metric=", 1)[1])


if __name__ == "__main__":
    unittest.main()
