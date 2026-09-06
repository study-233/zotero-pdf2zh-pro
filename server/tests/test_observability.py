from __future__ import annotations

import json
import threading
import unittest

from observability import TaskMetricsCollector


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
            clock=clock or FakeClock(),
        )

    def test_metrics_snapshot_covers_requests_cache_and_progress(self) -> None:
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
        self.assertNotIn("cost", metrics)
        self.assertEqual(progress["paragraphsPerMinute"], 120.0)
        self.assertEqual(progress["etaSeconds"], 30)

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
