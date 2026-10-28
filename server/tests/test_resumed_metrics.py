import copy
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from observability import TaskMetricsCollector, empty_metrics, merge_metrics
from task_manager import TaskManager, TaskRecord
from pdf2zh_pro.pdf import TranslationResult


def metrics(latencies, *, tokens=True):
    collector = TaskMetricsCollector(task_id='test', provider='test', model='test')
    for latency in latencies:
        collector.record_completed_request(kind='translation', latency_ms=latency, succeeded=True)
        if tokens:
            collector.record_usage(prompt_tokens=10, completion_tokens=5, cache_hit_tokens=0, cache_miss_tokens=10)
    return collector.snapshot()


class ResumeMetricsTests(unittest.TestCase):
    def test_zero_request_resume_preserves_previous_usage_and_latency(self):
        before = metrics([10, 20, 30])
        current = empty_metrics()
        merged = merge_metrics(before, current)
        self.assertEqual(merged['requests']['succeeded'], 3)
        self.assertEqual(merged['tokens']['total'], 45)
        self.assertEqual(merged['tokens']['availability'], 'complete')
        self.assertEqual(merged['requests']['p95LatencyMs'], 30)
        self.assertEqual(merged['requests']['active'], 0)
        self.assertEqual(before, metrics([10, 20, 30]))

    def test_weighted_latency_counters_and_unknown_usage_are_not_fabricated(self):
        before, current = metrics([10, 20]), metrics([100], tokens=False)
        original = copy.deepcopy(before)
        merged = merge_metrics(before, current)
        self.assertEqual(merged['requests']['averageLatencyMs'], 43.3)
        self.assertEqual(merged['requests']['p95LatencyMs'], 100)
        self.assertEqual(merged['requests']['statusCodes']['unknown'], 3)
        self.assertEqual(merged['tokens']['total'], 30)
        self.assertEqual(merged['tokens']['availability'], 'partial')
        self.assertEqual(merged['requests']['byKind']['translation']['succeeded'], 3)
        self.assertEqual(before, original)

    def test_current_activity_and_totals_use_distinct_scopes(self):
        before, current = metrics([10]), metrics([20])
        before.update(stageDurations={'Translate Paragraphs': 2}, validation={'accepted': 1})
        current.update(stageDurations={'Translate Paragraphs': 3}, validation={'accepted': 2})
        before['requests']['active'] = 8
        current['requests']['active'] = 1
        merged = merge_metrics(before, current)
        self.assertEqual(merged['requests']['active'], 1)
        self.assertEqual(merged['stageDurations']['Translate Paragraphs'], 5)
        self.assertEqual(merged['validation']['accepted'], 3)
        self.assertEqual(merged['currentExecution']['requests']['succeeded'], 1)
        self.assertEqual(merge_metrics(merged, empty_metrics())['tokens']['total'], 30)

    def test_missing_legacy_latency_never_becomes_zero(self):
        previous = empty_metrics()
        previous['requests'].update(succeeded=1, attempts=1)
        previous['requests'].pop('latencyTotalMs')
        previous['requests'].pop('latencySamplesMs')
        self.assertIsNone(merge_metrics(previous, metrics([100]))['requests']['averageLatencyMs'])

    def test_repeated_snapshots_and_second_restart_never_count_a_run_twice(self):
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as cleanup:
            root = Path(temporary)
            source = root / 'paper.pdf'
            source.write_bytes(b'%PDF-1.4\n')
            previous = metrics([10, 20])
            current = metrics([100])
            async def translate(payload, task_id, **callbacks):
                callbacks['metrics_callback'](current)
                callbacks['metrics_callback'](current)
                return TranslationResult(files={})
            manager = TaskManager(root / 'tasks.json', translator=translate)
            # Close workers and drain diagnostics before removing their directory.
            cleanup.callback(manager.diagnostics.flush, timeout=2)
            cleanup.callback(manager.close)
            manager._tasks['one'] = TaskRecord('one', 'paper.pdf', 'openai', ['dual'],
                {'input_path': str(source)}, root, status='queued', metrics=previous)
            manager._run_task('one')
            self.assertEqual(manager.get_task('one')['metrics']['requests']['succeeded'], 3)
            manager.close()
            restored = TaskManager(root / 'tasks.json', translator=translate)
            cleanup.callback(restored.diagnostics.flush, timeout=2)
            cleanup.callback(restored.close)
            record = restored._tasks['one']
            record.status = 'queued'
            restored._run_task('one')
            self.assertEqual(restored.get_task('one')['metrics']['requests']['succeeded'], 4)
            record.status = 'failed'
            with patch.object(restored, '_start_worker_locked'):
                restored.retry_task('one')
            self.assertEqual(restored.get_task('one')['metrics']['requests']['succeeded'], 0)
