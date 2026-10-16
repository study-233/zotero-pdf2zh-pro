from __future__ import annotations

import json
import sqlite3
import threading
import unittest

from observability import TaskMetricsCollector, reserve_benchmark_request


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class ObservabilityTests(unittest.TestCase):
    def test_call_ids_distinguish_concurrent_starts_at_same_clock_tick(self):
        collector = self.make_collector()
        first, second = collector.request_started(), collector.request_started(kind='term_extraction')
        self.assertNotEqual(first.call_id, second.call_id)
        collector.request_finished(first, succeeded=True)
        collector.request_finished(first, succeeded=True)
        self.assertEqual(collector.snapshot()['requests']['active'], 1)
        collector.request_finished(second, succeeded=False)
        snapshot = collector.snapshot()
        self.assertEqual(snapshot['requests']['attempts'], 2)
        self.assertEqual(snapshot['requests']['succeeded'], 1)
        self.assertEqual(snapshot['requests']['failed'], 1)
        self.assertEqual(snapshot['requests']['byPurpose']['term_extraction']['attempts'], 1)
        self.assertEqual(snapshot['requests']['byKind']['translation']['attempts'], 2)

    def test_usage_and_validation_are_idempotent_per_call(self):
        events = []
        collector = TaskMetricsCollector(task_id='test', provider='test', model='test', journal=events.append)
        started = collector.request_started(kind='term_extraction', operation_id='batch-1', paragraph_ids=['p1', 'p2'])
        for _ in range(2):
            collector.record_usage(prompt_tokens=100, completion_tokens=20, cache_hit_tokens=60, cache_miss_tokens=40, call=started)
            collector.request_finished(started, succeeded=True)
            collector.record_validation(started, 'partial', accepted=1, rejected=1)
        value = collector.snapshot()
        self.assertEqual(value['tokens']['input'], 100)
        self.assertEqual(value['tokens']['byPurpose']['term_extraction']['input'], 100)
        self.assertEqual(value['validation'], dict(accepted=0, partial=1, rejected=0))
        self.assertEqual([e['event'] for e in events], ['request_started', 'usage', 'request_finished', 'validation'])
        self.assertEqual({e['callId'] for e in events}, {started.call_id})

    def test_missing_usage_is_unknown_not_a_free_request(self):
        collector = self.make_collector()
        call = collector.request_started()
        collector.request_finished(call, succeeded=False, finish_reason='cancelled')
        self.assertIsNone(collector.snapshot()['tokens']['input'])
        self.assertEqual(collector.snapshot()['tokens']['availability'], 'unavailable')

    def test_late_cumulative_usage_updates_failed_call_without_adding_a_second_call(self):
        collector = self.make_collector()
        call = collector.request_started()
        collector.request_finished(call, succeeded=False, finish_reason='failed')
        for incoming, outgoing in [(10, 2), (10, 3), (10, 2)]:
            collector.record_usage(prompt_tokens=incoming, completion_tokens=outgoing,
                                   cache_hit_tokens=None, cache_miss_tokens=None, call=call)
        value = collector.snapshot()
        self.assertEqual(value['tokens']['total'], 13)
        self.assertEqual(value['tokens']['availability'], 'complete')
        self.assertEqual(value['requests']['attempts'], 1)
        self.assertEqual(collector._usage.calls, 1)

    def test_partial_batch_token_cost_is_not_multiplied_by_paragraph_count(self):
        collector = self.make_collector()
        call = collector.request_started(paragraph_ids=['one', 'two', 'three'])
        collector.record_usage(prompt_tokens=90, completion_tokens=30, cache_hit_tokens=None, cache_miss_tokens=None, call=call)
        collector.request_finished(call, succeeded=True, batch_size=3)
        collector.record_validation(call, 'partial', accepted=2, rejected=1)
        collector.set_translation_summary(dict(total=3, succeeded=2, failed=1, skipped=0, pending=0))
        self.assertEqual(collector.snapshot()['tokens']['total'], 120)
        self.assertEqual(collector.snapshot()['translationSummary']['failed'], 1)

    def test_journal_failure_is_visible_without_breaking_normal_translation(self):
        def broken(_):
            raise OSError('unavailable')
        collector = TaskMetricsCollector(task_id='test', provider='test', model='test', journal=broken)
        call = collector.request_started()
        collector.request_finished(call, succeeded=True)
        self.assertEqual(collector.snapshot()['requests']['succeeded'], 1)
        self.assertGreater(collector.snapshot()['journal']['errors'], 0)

    def test_benchmark_request_limit_is_shared_across_collectors(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', {
                'PDF2ZH_BENCHMARK_LEDGER_DIR': directory, 'PDF2ZH_BENCHMARK_MAX_REQUESTS': '1'}):
            self.make_collector().request_started()
            with self.assertRaisesRegex(RuntimeError, 'benchmark_request_limit'):
                self.make_collector().request_started()

    def test_benchmark_budget_closes_connections_after_success_and_limit(self):
        import tempfile
        from unittest.mock import patch
        connect, connections = sqlite3.connect, []

        def tracked_connect(*args, **kwargs):
            connection = connect(*args, **kwargs)
            connections.append(connection)
            return connection

        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', {
                'PDF2ZH_BENCHMARK_LEDGER_DIR': directory, 'PDF2ZH_BENCHMARK_MAX_REQUESTS': '1'}):
            try:
                with patch('observability.sqlite3.connect', side_effect=tracked_connect):
                    reserve_benchmark_request()
                    with self.assertRaisesRegex(RuntimeError, 'benchmark_request_limit'):
                        reserve_benchmark_request()
                self.assertEqual(len(connections), 2)
                for connection in connections:
                    with self.assertRaises(sqlite3.ProgrammingError):
                        connection.execute('SELECT 1')
            finally:
                for connection in connections:
                    connection.close()

    def test_first_paragraph_latency_is_observed_once(self):
        clock = FakeClock()
        collector = self.make_collector(clock)
        clock.advance(2)
        collector.first_paragraph_completed()
        clock.advance(5)
        collector.first_paragraph_completed()
        self.assertEqual(collector.snapshot()['firstParagraphMs'], 2000)

    def test_cancelled_request_has_its_own_count_and_latency_without_success_or_failure(self):
        clock = FakeClock()
        collector = self.make_collector(clock)
        started = collector.request_started()
        clock.advance(.25)
        collector.request_finished(started, succeeded=False, finish_reason="cancelled")
        stats = collector.snapshot()["requests"]
        self.assertEqual((stats["attempts"], stats["succeeded"], stats["failed"], stats["cancelled"]), (1, 0, 0, 1))
        self.assertEqual(stats["averageLatencyMs"], 250)
        self.assertEqual(stats["byKind"]["translation"]["cancelled"], 1)

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
