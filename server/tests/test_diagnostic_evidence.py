"""Fault injection for the persisted task → execution → request → paragraph chain."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid
import zipfile

from diagnostic_evidence import EvidenceLedger, FILENAME, analyze, analyze_bundle, call_ref, link_failures, read_evidence
from diagnostics import safe_fields
from observability import TaskMetricsCollector


def paragraph():
    return 'p-' + uuid.uuid4().hex


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.ledger = EvidenceLedger(self.directory, 'task-one', 3)
        self.addCleanup(self.ledger.close, 'failed')
        self.collector = TaskMetricsCollector(task_id='task-one', provider='openai', model='test-model',
                                              clock=lambda: 100, evidence=self.ledger)

    def finish(self, status='incomplete', **kwargs):
        self.ledger.close(status, **kwargs)
        return read_evidence(self.directory, 'task-one', 3, status)

    def test_partial_batch_retry_and_fallback_keep_parent_operation_and_rejections(self):
        first, second = paragraph(), paragraph()
        batch = self.collector.request_started(operation_id='private debug source', paragraph_refs=[first, second])
        self.collector.request_finished(batch, succeeded=True, status_code=200, batch_size=2)
        self.collector.record_validation(batch, 'partial', invalid_outputs={1: 'placeholder_mismatch'})
        fallback = self.collector.request_started(operation_id='fallback', paragraph_refs=[second],
                                                 parent_call=batch, retry_cause='batch_partial')
        self.collector.request_finished(fallback, succeeded=True, status_code=200)
        self.collector.record_validation(fallback, 'rejected', code='unchanged_translation')
        retry = self.collector.request_started(operation_id='fallback', paragraph_refs=[second],
                                              parent_call=fallback, retry_cause='output_validation')
        self.collector.request_finished(retry, succeeded=True, status_code=200)
        self.collector.record_validation(retry, 'rejected', code='target_language_missing')
        self.ledger.paragraphs([{'paragraphRef': first, 'paragraphState': 'succeeded'},
                               {'paragraphRef': second, 'page': 2, 'paragraphState': 'failed',
                                'validationCode': 'target_language_missing', 'missingPlaceholderCount': 1}])
        graph = self.finish()
        self.assertEqual(graph['evidenceStatus'], 'available')
        calls = {row['callId']: row for row in graph['requests']}
        self.assertNotEqual(call_ref(batch), call_ref(fallback))  # Same clock; identity is not a timestamp.
        self.assertEqual(calls[call_ref(batch)]['validationFailures'],
                         [{'paragraphRef': second, 'validationCode': 'placeholder_mismatch'}])
        self.assertEqual(calls[call_ref(retry)]['parentCallId'], call_ref(fallback))
        self.assertEqual(calls[call_ref(fallback)]['parentCallId'], call_ref(batch))
        self.assertEqual(calls[call_ref(retry)]['operationId'], calls[call_ref(fallback)]['operationId'])
        self.assertNotEqual(calls[call_ref(batch)]['operationId'], calls[call_ref(fallback)]['operationId'])
        task = {'taskId': 'task-one', 'attempt': 3, 'translationSummary': {'failed': 1},
                'failedParagraphs': [{'paragraphRef': second, 'page': 2, 'validationCode': 'target_language_missing'}]}
        link_failures(task, graph)
        self.assertEqual(set(task['failedParagraphs'][0]['callRefs']), set(calls))
        findings = analyze([task], {'taskEvidence': [graph]})['findings']
        self.assertTrue(any(row.get('linkedFailures') == 1 for row in findings))
        self.assertFalse(any(row['findingCode'] == 'provider_request_failed' for row in findings))
        self.assertTrue(any(row.get('validationCode') == 'placeholder_mismatch' for row in findings))
        self.assertNotIn('private debug source', (self.directory / FILENAME).read_bytes().decode('utf8', errors='ignore'))

    def test_concurrent_calls_are_unique_and_finished_once(self):
        def request(index):
            call = self.collector.request_started(paragraph_refs=[paragraph()])
            self.collector.request_finished(call, succeeded=True, status_code=200)
            self.collector.request_finished(call, succeeded=False, status_code=503)
            return call_ref(call)
        with ThreadPoolExecutor(8) as pool:
            refs = list(pool.map(request, range(24)))
        graph = self.finish('completed')
        self.assertEqual(len(set(refs)), 24)
        self.assertEqual(len(graph['requests']), 24)
        self.assertTrue(all(row['requestState'] == 'succeeded' for row in graph['requests']))
        self.assertEqual(graph['executions'][0]['requestSummary']['attempts'], 24)

    def test_provider_retry_and_cancel_are_distinct_from_translation_validation(self):
        ref = paragraph()
        failed = self.collector.request_started(paragraph_refs=[ref])
        self.collector.request_finished(failed, succeeded=False, status_code=429, error_type='RateLimitError')
        retry = self.collector.request_started(paragraph_refs=[ref], parent_call=failed, retry_cause='provider_error')
        self.collector.request_finished(retry, succeeded=False, finish_reason='cancelled')
        graph = self.finish('cancelled')
        self.assertEqual({row['requestState'] for row in graph['requests']}, {'failed', 'cancelled'})
        self.assertEqual(next(row for row in graph['requests'] if row['callId'] == call_ref(retry))['retryCause'], 'provider_error')
        findings = analyze([{'taskId': 'task-one'}], {'taskEvidence': [graph]})['findings']
        self.assertEqual([row['findingCode'] for row in findings], ['provider_rate_limited'])

    def test_graceful_cancellation_with_an_unconfirmed_call_is_partial(self):
        self.collector.request_started(paragraph_refs=[paragraph()])
        graph = self.finish('cancelled')
        self.assertEqual(graph['requests'][0]['requestState'], 'interrupted')
        self.assertEqual(graph['evidenceStatus'], 'partial')

    def test_initialization_replay_and_cumulative_usage_are_explicit(self):
        self.collector.record_completed_request(kind='initialization', succeeded=True, latency_ms=20,
                                                status_code=200, protocol='responses')
        call = self.collector.request_started(paragraph_refs=[paragraph()])
        self.collector.record_usage(call=call, prompt_tokens=12, completion_tokens=2, cache_hit_tokens=None, cache_miss_tokens=None)
        self.collector.record_usage(call=call, prompt_tokens=12, completion_tokens=5, reasoning_tokens=1, cache_hit_tokens=None, cache_miss_tokens=None)
        self.collector.record_usage(call=call, prompt_tokens=12, completion_tokens=2, cache_hit_tokens=None, cache_miss_tokens=None)
        self.collector.request_finished(call, succeeded=True, status_code=200)
        graph = self.finish('completed')
        replay = next(row for row in graph['requests'] if row['purpose'] == 'initialization')
        self.assertTrue(replay['observedReplay'])
        self.assertEqual(replay['paragraphRefs'], [])
        live = next(row for row in graph['requests'] if row['callId'] == call_ref(call))
        self.assertEqual((live['input'], live['output'], live['reasoning']), (12, 5, 1))

    def test_cache_and_checkpoint_reuse_do_not_invent_provider_requests(self):
        refs = [paragraph(), paragraph()]
        self.collector.local_cache_hit()
        self.collector.cache_reused('cache operation', [refs[0]])
        self.ledger.paragraphs([{'paragraphRef': refs[0], 'paragraphState': 'succeeded'},
                               {'paragraphRef': refs[1], 'paragraphState': 'succeeded', 'outcomeSource': 'checkpoint'}])
        graph = self.finish('completed')
        self.assertEqual(graph['requests'], [])
        self.assertEqual(graph['executions'][0]['requestSummary']['cacheHits'], 1)
        self.assertEqual({row['outcomeSource'] for row in graph['paragraphs']}, {'cache', 'checkpoint'})
        result = analyze([{'taskId': 'task-one'}], {'taskEvidence': [graph]})
        self.assertEqual(result['findings'][0]['cacheReuseCount'], 2)

    def test_multiple_attempts_and_executions_survive_service_restart(self):
        ref = paragraph()
        old = self.collector.request_started(paragraph_refs=[ref])
        self.collector.request_finished(old, succeeded=False, status_code=503)
        first = self.finish('failed')
        later = EvidenceLedger(self.directory, 'task-one', 4)
        later.paragraphs([{'paragraphRef': ref, 'paragraphState': 'succeeded', 'outcomeSource': 'checkpoint'}])
        later.close('completed')
        graph = read_evidence(self.directory, 'task-one', 4, 'completed')
        self.assertEqual({row['attempt'] for row in graph['executions']}, {3, 4})
        self.assertNotEqual(first['executions'][0]['executionId'], later.execution_id)
        self.assertEqual(graph['requests'][0]['executionId'], first['executions'][0]['executionId'])

    def test_hard_exit_marks_unfinished_calls_interrupted_without_claiming_failure(self):
        self.collector.request_started(paragraph_refs=[paragraph()])
        deadline = time.monotonic() + 2
        while self.ledger.pending.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(.005)
        # Parent observed worker termination; writer never received a terminal event.
        graph = read_evidence(self.directory, 'task-one', 3, 'cancelled')
        self.assertEqual(graph['requests'][0]['requestState'], 'interrupted')
        self.assertEqual(graph['executions'][0]['executionState'], 'interrupted')
        self.assertEqual(graph['evidenceStatus'], 'partial')
        self.assertEqual(self.collector.snapshot()['requests']['failed'], 0)

    def test_failure_snapshot_survives_a_missing_tail_without_fabricated_calls(self):
        graph = self.finish()
        ref = paragraph()
        task = {'taskId': 'task-one', 'attempt': 3, 'failedParagraphs': [
            {'paragraphRef': ref, 'page': 1, 'validationCode': 'invalid_json'}]}
        link_failures(task, graph)
        self.assertEqual(graph['evidenceStatus'], 'partial')
        self.assertEqual(graph['paragraphs'][0]['recordOrigin'], 'checkpoint_snapshot')
        self.assertNotIn('executionId', graph['paragraphs'][0])
        self.assertEqual(task['failedParagraphs'][0]['callRefs'], [])

    def test_caps_evict_detail_but_keep_independent_execution_totals_and_failures(self):
        self.ledger.max_rows = 12
        self.ledger.max_bytes = 6000
        failed_ref = paragraph()
        failed = self.collector.request_started(paragraph_refs=[failed_ref])
        self.collector.request_finished(failed, succeeded=False, status_code=503)
        self.ledger.paragraphs([{'paragraphRef': failed_ref, 'paragraphState': 'failed'}])
        for _ in range(20):
            call = self.collector.request_started(paragraph_refs=[paragraph()])
            self.collector.request_finished(call, succeeded=True, status_code=200)
        graph = self.finish()
        self.assertEqual(graph['evidenceStatus'], 'partial')
        self.assertGreater(graph['omittedRequests'], 0)
        self.assertTrue(any(row.get('paragraphRef') == failed_ref for row in graph['paragraphs']))
        self.assertEqual(graph['executions'][0]['requestSummary']['attempts'], 21)
        with closing(sqlite3.connect(self.directory / FILENAME)) as connection:
            used, count = connection.execute('SELECT SUM(bytes),COUNT(*) FROM records').fetchone()
        self.assertLessEqual(used, 6000)
        self.assertLessEqual(count, 12)

    def test_export_cap_and_missing_ancestors_are_explicit(self):
        first = self.collector.request_started(paragraph_refs=[paragraph()])
        second = self.collector.request_started(parent_call=first, retry_cause='provider_error')
        self.collector.request_finished(second, succeeded=True)
        self.finish('completed')
        small = read_evidence(self.directory, 'task-one', 3, 'completed', max_bytes=1)
        self.assertEqual(small['evidenceStatus'], 'partial')
        self.assertGreater(small['omittedRequests'], 0)
        graph = read_evidence(self.directory, 'task-one', 3, 'completed')
        graph['requests'] = [row for row in graph['requests'] if row['callId'] == call_ref(second)]
        link_failures({'attempt': 3}, graph)
        self.assertEqual(graph['evidenceStatus'], 'partial')

    def test_background_unavailable_or_queue_full_cannot_block_translation(self):
        self.finish()
        with patch('diagnostic_evidence.threading.Thread.start', side_effect=RuntimeError('no threads')):
            ledger = EvidenceLedger(self.directory / 'disabled', 'task-two')
        self.assertGreater(ledger.close('failed', timeout=.02)['storageErrors'], 0)
        ledger.disabled = False  # Frozen writer fault; exercise bounded enqueue.
        ledger.stop.clear()
        for _ in range(600): ledger.execution(executionState='running')
        self.assertLessEqual(ledger.pending.qsize(), 512)
        self.assertGreater(ledger.health()['queueDropped'], 0)
        start = time.monotonic()
        self.assertGreater(ledger.close('failed', timeout=.02)['unflushedEvents'], 0)
        self.assertLess(time.monotonic() - start, .2)

    def test_readonly_export_during_writes_and_corrupt_or_wrong_task_stores(self):
        self.collector.request_started(paragraph_refs=[paragraph()])
        for _ in range(10):
            graph = read_evidence(self.directory, 'task-one', 3, 'running')
            self.assertIn(graph['evidenceStatus'], {'available', 'missing', 'unavailable'})
        self.finish()
        self.assertEqual(read_evidence(self.directory, 'other-task', 1, 'failed')['evidenceStatus'], 'unavailable')
        (self.directory / FILENAME).write_bytes(b'invalid sqlite private text')
        graph = read_evidence(self.directory, 'task-one', 3, 'failed')
        self.assertEqual(graph['evidenceCode'], 'invalid_evidence_store')
        self.assertNotIn('private text', json.dumps(graph))

    def test_privacy_filter_retains_only_valid_opaque_refs_and_registered_fields(self):
        ref = paragraph()
        self.assertEqual(safe_fields({'paragraphRefs': [ref, 'private text', 'deadbeef'],
            'paragraphRef': 'source-sha256', 'callId': 'request body', 'prompt': 'sk-secret'}),
            {'paragraphRefs': [ref]})
        self.ledger.paragraphs([{'paragraphRef': ref, 'paragraphState': 'failed',
                                'paragraphId': 'private debug id', 'source': 'private text',
                                'reason': 'private provider response', 'apiKey': 'sk-secret'}])
        graph = self.finish()
        raw = (self.directory / FILENAME).read_bytes()
        for secret in (b'private debug id', b'private text', b'private provider response', b'sk-secret'):
            self.assertNotIn(secret, raw)
            self.assertNotIn(secret.decode(), json.dumps(graph))

    def test_offline_new_and_old_zip_analysis_requires_no_running_service(self):
        ref = paragraph()
        call = self.collector.request_started(paragraph_refs=[ref])
        self.collector.request_finished(call, succeeded=True, status_code=200)
        self.collector.record_validation(call, 'rejected', code='invalid_json')
        graph = self.finish()
        for new in (False, True):
            path = self.directory / ('new.zip' if new else 'old.zip')
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('task.json', json.dumps({'taskId': 'task-one', 'attempt': 3}))
                if new: archive.writestr('evidence.json', json.dumps({'schemaVersion': 1, 'taskEvidence': [graph]}))
            result = analyze_bundle(path)
            codes = {row['findingCode'] for row in result['findings']}
            self.assertIn('validation_failed' if new else 'evidence_unavailable', codes)
            command = [sys.executable, '-m', 'diagnostic_evidence', str(path)]
            completed = subprocess.run(command, capture_output=True, text=True, timeout=5)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(completed.stdout), result)
        with zipfile.ZipFile(self.directory / 'future.zip', 'w') as archive:
            archive.writestr('server.json', '{"schemaVersion":2}')
        with self.assertRaisesRegex(ValueError, 'unsupported_diagnostic_schema'):
            analyze_bundle(self.directory / 'future.zip')

    def test_execution_retention_is_bounded_and_reports_omissions(self):
        self.finish('failed')
        with patch('diagnostic_evidence.MAX_EXECUTIONS', 2):
            for attempt in range(4, 7):
                ledger = EvidenceLedger(self.directory, 'task-one', attempt)
                ledger.close('completed')
        graph = read_evidence(self.directory, 'task-one', 6, 'completed')
        self.assertEqual(len(graph['executions']), 2)
        self.assertEqual(graph['omittedExecutions'], 2)
        self.assertEqual(graph['evidenceStatus'], 'partial')

    def test_oversized_links_and_unknown_validation_do_not_silently_disappear(self):
        refs = [paragraph() for _ in range(270)]
        call = self.collector.request_started(paragraph_refs=refs)
        self.collector.request_finished(call, succeeded=True)
        self.collector.record_validation(call, 'rejected', invalid_outputs={0: 'private unknown validation'})
        graph = self.finish('incomplete')
        self.assertEqual(graph['requests'][0]['omittedLinks'], 14)
        self.assertEqual(graph['evidenceStatus'], 'partial')
        self.assertEqual(graph['requests'][0]['validationFailures'][0]['validationCode'], 'unknown_validation')
        self.assertNotIn('private unknown validation', json.dumps(graph))

    def test_future_store_is_preserved_and_writer_failure_is_reported(self):
        self.finish('completed')
        # The transaction context commits; closing releases the Windows file handle.
        with closing(sqlite3.connect(self.directory / FILENAME)) as connection, connection:
            connection.execute("UPDATE metadata SET value='2' WHERE key='version'")
        newer = EvidenceLedger(self.directory, 'task-one', 4)
        health = newer.close('failed')
        self.assertGreater(health['storageErrors'], 0)
        with closing(sqlite3.connect(self.directory / FILENAME)) as connection:
            self.assertEqual(connection.execute("SELECT value FROM metadata WHERE key='version'").fetchone()[0], '2')
        self.assertEqual(read_evidence(self.directory, 'task-one', 4, 'failed')['evidenceStatus'], 'unavailable')

    def test_offline_parser_rejects_bombs_and_handles_malformed_shapes(self):
        path = self.directory / 'bundle.zip'
        with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('task.json', b' ' * (20 * 1024 * 1024 + 1))
        with self.assertRaisesRegex(ValueError, 'too_large'):
            analyze_bundle(path)
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('task.json', '{"taskId":"malformed","translationSummary":[]}')
            archive.writestr('evidence.json', '{"taskEvidence":[{"taskId":"malformed","requests":{},"paragraphs":{}}]}')
        self.assertEqual(analyze_bundle(path)['findings'][0]['findingCode'], 'evidence_unavailable')

    def test_real_worker_termination_leaves_readable_committed_evidence(self):
        directory = self.directory / 'worker'
        directory.mkdir()
        ready = directory / 'ready'
        script = '''
import sys,time
from pathlib import Path
from diagnostic_evidence import EvidenceLedger
from observability import TaskMetricsCollector
directory=Path(sys.argv[1])
ledger=EvidenceLedger(directory,'child',1)
collector=TaskMetricsCollector(task_id='child',provider='openai',model='test',evidence=ledger)
collector.request_started(paragraph_refs=['p-'+'a'*32])
while ledger.pending.unfinished_tasks:time.sleep(.005)
(directory/'ready').touch()
time.sleep(60)
'''
        process = subprocess.Popen([sys.executable, '-c', script, str(directory)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(ready.exists(), 'worker did not commit evidence')
            process.terminate()
            process.communicate(timeout=5)
            graph = read_evidence(directory, 'child', 1, 'cancelled')
            self.assertEqual(graph['requests'][0]['requestState'], 'interrupted')
            self.assertEqual(graph['evidenceStatus'], 'partial')
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)


if __name__ == '__main__':
    unittest.main()
