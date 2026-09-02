from __future__ import annotations

import json
import tempfile
import threading
import unittest
from datetime import datetime
from datetime import timezone
from pathlib import Path

from observability import DeepSeekPricingManager
from observability import DeepSeekPricing
from observability import TaskMetricsCollector
from observability import parse_deepseek_pricing_manifest
from observability import resolve_deepseek_pricing


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class ObservabilityTests(unittest.TestCase):
    def make_collector(self, clock: FakeClock | None = None):
        return TaskMetricsCollector(
            task_id="task-1",
            provider="deepseek",
            model="deepseek-chat",
            pricing=DeepSeekPricing(0.5, 2.0, 8.0, "CNY", "test"),
            clock=clock or FakeClock(),
        )

    def test_metrics_snapshot_covers_requests_cache_cost_and_progress(self) -> None:
        clock = FakeClock()
        collector = self.make_collector(clock)
        started = collector.request_started()
        clock.advance(0.25)
        collector.request_finished(started, succeeded=False, status_code=429)
        collector.retry_scheduled()
        collector.local_cache_hit()
        collector.local_cache_miss()
        collector.record_usage(
            prompt_tokens=300,
            completion_tokens=50,
            cache_hit_tokens=200,
            cache_miss_tokens=100,
        )
        progress_clock = FakeClock()
        progress_collector = self.make_collector(progress_clock)
        progress_collector.update_progress(
            {
                "type": "progress_start",
                "stage": "Translate Paragraphs",
                "stage_current": 0,
                "overall_progress": 10,
            }
        )
        progress_clock.advance(15)
        progress_collector.update_progress(
            {
                "type": "progress_update",
                "stage": "Translate Paragraphs",
                "stage_current": 30,
                "overall_progress": 40,
            }
        )

        metrics = collector.snapshot()
        progress = progress_collector.snapshot()["throughput"]
        self.assertEqual(metrics["requests"]["attempts"], 1)
        self.assertEqual(metrics["requests"]["retries"], 1)
        self.assertEqual(metrics["localCache"]["hitRate"], 0.5)
        self.assertEqual(metrics["providerCache"]["hitTokens"], 200)
        self.assertEqual(metrics["cost"]["accuracy"], "exact-tokens")
        self.assertEqual(progress["paragraphsPerMinute"], 120.0)
        self.assertEqual(progress["etaSeconds"], 30)

    def test_pricing_contract_covers_boundaries_custom_and_fallback(self) -> None:
        for hour, minute, expected in (
            (0, 59, 6.05),
            (1, 0, 12.1),
            (4, 0, 6.05),
            (6, 0, 12.1),
        ):
            with self.subTest(hour=hour, minute=minute):
                collector = TaskMetricsCollector(
                    task_id="pricing",
                    provider="deepseek",
                    model="deepseek-v4-flash",
                    pricing=resolve_deepseek_pricing("deepseek-v4-flash", {}),
                    utc_now=lambda h=hour, m=minute: datetime(
                        2026, 8, 17, h, m, tzinfo=timezone.utc
                    ),
                )
                collector.record_usage(
                    prompt_tokens=2_000_000,
                    completion_tokens=1_000_000,
                    cache_hit_tokens=1_000_000,
                    cache_miss_tokens=1_000_000,
                )
                self.assertEqual(collector.snapshot()["cost"]["amount"], expected)

        weekend = TaskMetricsCollector(
            task_id="weekend-pricing",
            provider="deepseek",
            model="deepseek-v4-flash",
            pricing=resolve_deepseek_pricing("deepseek-v4-flash", {}),
            utc_now=lambda: datetime(2026, 8, 22, 1, 0, tzinfo=timezone.utc),
        )
        weekend.record_usage(
            prompt_tokens=2_000_000,
            completion_tokens=1_000_000,
            cache_hit_tokens=1_000_000,
            cache_miss_tokens=1_000_000,
        )
        self.assertEqual(weekend.snapshot()["cost"]["amount"], 6.05)

        vision = resolve_deepseek_pricing("deepseek-v4-flash-vision-exp", {})
        self.assertIsNotNone(vision)
        self.assertEqual(vision.off_peak.cache_hit_input, 0.05)
        self.assertEqual(vision.source, "bundled")
        self.assertEqual(vision.version, "2026-08-17-cny-tiered-r2")

        custom = resolve_deepseek_pricing(
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
        self.assertEqual(custom, DeepSeekPricing(1, 2, 3, "USD", "custom-v1"))

        fallback = self.make_collector()
        fallback.record_usage(
            prompt_tokens=300,
            completion_tokens=0,
            cache_hit_tokens=None,
            cache_miss_tokens=None,
        )
        self.assertEqual(fallback.snapshot()["cost"]["accuracy"], "fallback")

    def test_pricing_manager_updates_caches_and_rejects_invalid_data(self) -> None:
        manifest_path = (
            Path(__file__).resolve().parents[1]
            / "pdf2zh_next"
            / "deepseek_pricing.json"
        )
        bundled = json.loads(manifest_path.read_text(encoding="utf-8"))
        remote = dict(bundled)
        remote["version"] = "remote-v1"
        remote["updatedAt"] = "2026-09-02T10:00:00+08:00"
        remote_payload = json.dumps(remote).encode("utf-8")

        with tempfile.TemporaryDirectory() as temp_dir:
            manager = DeepSeekPricingManager()
            self.assertEqual(
                manager.refresh_once(temp_dir, lambda: remote_payload),
                "updated",
            )
            self.assertEqual(manager.resolve("deepseek-chat").version, "remote-v1")
            self.assertEqual(manager.resolve("deepseek-chat").source, "remote")
            self.assertEqual(
                manager.refresh_once(temp_dir, lambda: remote_payload),
                "unchanged",
            )

            cache_path = Path(temp_dir) / "pricing" / "deepseek.json"
            self.assertEqual(cache_path.read_bytes(), remote_payload)
            reloaded = DeepSeekPricingManager()
            self.assertTrue(reloaded.load_cache(temp_dir))
            self.assertEqual(reloaded.resolve("deepseek-v4-pro").version, "remote-v1")

            cache_path.write_text("{broken", encoding="utf-8")
            fallback = DeepSeekPricingManager()
            self.assertFalse(fallback.load_cache(temp_dir))
            self.assertEqual(fallback.resolve("deepseek-chat").source, "bundled")

            stale = dict(bundled)
            stale["version"] = "stale-v1"
            stale["updatedAt"] = "2026-01-01T00:00:00+08:00"
            cache_path.write_text(json.dumps(stale), encoding="utf-8")
            self.assertFalse(fallback.load_cache(temp_dir))
            self.assertEqual(fallback.resolve("deepseek-chat").source, "bundled")

            previous = manager.resolve("deepseek-chat")
            with self.assertRaises(TimeoutError):
                manager.refresh_once(
                    temp_dir,
                    lambda: (_ for _ in ()).throw(TimeoutError()),
                )
            self.assertIs(manager.resolve("deepseek-chat"), previous)

    def test_pricing_manifest_validation_rejects_negative_rates(self) -> None:
        manifest_path = (
            Path(__file__).resolve().parents[1]
            / "pdf2zh_next"
            / "deepseek_pricing.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["models"]["deepseek-v4-pro"]["peak"]["output"] = -1
        with self.assertRaisesRegex(ValueError, "non-negative"):
            parse_deepseek_pricing_manifest(manifest, source="remote")

    def test_running_task_keeps_its_pricing_snapshot(self) -> None:
        manifest_path = (
            Path(__file__).resolve().parents[1]
            / "pdf2zh_next"
            / "deepseek_pricing.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manager = DeepSeekPricingManager()
        original = manager.resolve("deepseek-v4-flash")
        collector = TaskMetricsCollector(
            task_id="frozen-pricing",
            provider="deepseek",
            model="deepseek-v4-flash",
            pricing=original,
            utc_now=lambda: datetime(2026, 8, 17, 0, 0, tzinfo=timezone.utc),
        )
        manifest["version"] = "future-price"
        manifest["models"]["deepseek-v4-flash"]["offPeak"]["output"] = 99
        with tempfile.TemporaryDirectory() as temp_dir:
            manager.refresh_once(
                temp_dir,
                lambda: json.dumps(manifest).encode("utf-8"),
            )
        collector.record_usage(
            prompt_tokens=0,
            completion_tokens=1_000_000,
            cache_hit_tokens=0,
            cache_miss_tokens=0,
        )
        self.assertEqual(collector.snapshot()["cost"]["amount"], 4.5)
        self.assertEqual(manager.resolve("deepseek-v4-flash").version, "future-price")

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

    def test_metric_log_payload_excludes_sensitive_fields(self) -> None:
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
