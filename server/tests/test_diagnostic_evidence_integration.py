"""Verify native provider, recovery and export integrations with isolated clients."""
import json
from pathlib import Path
import tempfile
import unittest

from diagnostic_evidence import EvidenceLedger, analyze, read_evidence
from observability import TaskMetricsCollector
from test_diagnostic_evidence import paragraph


class NativeEvidenceTests(unittest.TestCase):
    def test_global_export_budget_prefers_evidence_and_reports_omitted_tasks(self):
        from task_manager import TaskManager, TaskRecord
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        manager = TaskManager()
        failures = [{'page': index, 'attempts': 4, 'errorType': 'InvalidTranslation',
                     'validationCode': 'placeholder_mismatch', 'missingPlaceholderCount': 2}
                    for index in range(2000)]
        for index in range(40):
            record = TaskRecord(f'task-{index}', 'private.pdf', 'openai', ['dual'], {}, directory,
                                status='incomplete', failed_paragraphs=failures)
            manager._tasks[record.task_id] = record
        exported = manager.export_diagnostics()
        self.assertLess(len(json.dumps(exported, ensure_ascii=False).encode()), 10 * 1024 * 1024)
        self.assertTrue(exported['truncated'])
        self.assertEqual(len(exported['evidence']['taskEvidence']), 3)
        self.assertEqual(exported['evidence']['omittedTasks'], 37)

    def test_chatgpt_refresh_links_the_rejected_and_retried_calls(self):
        from test_chatgpt_refresh import UnauthorizedRecoveryTests
        helper = UnauthorizedRecoveryTests()
        self.addCleanup(helper.doCleanups)
        helper.setUp()
        ledger = EvidenceLedger(helper.directory / 'evidence', 'refresh', 1)
        self.addCleanup(ledger.close, 'failed')
        collector = TaskMetricsCollector(task_id='refresh', provider='chatgpt', model='m', evidence=ledger)
        adapter = helper.adapter(helper.client(), collector)
        ref = paragraph()
        adapter.llm_translate('source', ignore_cache=True, rate_limit_params={'diagnostic_paragraph_refs': [ref]})
        ledger.close('completed')
        graph = read_evidence(helper.directory / 'evidence', 'refresh', 1, 'completed')
        calls = sorted(graph['requests'], key=lambda row: row['callId'])
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]['statusCode'], 401)
        self.assertEqual(calls[1]['parentCallId'], calls[0]['callId'])
        self.assertEqual(calls[1]['retryCause'], 'auth_refresh')
        self.assertTrue(all(row['paragraphRefs'] == [ref] for row in calls))
        self.assertEqual(calls[1]['input'], 15)

    def test_codex_provider_error_and_paragraph_links_survive_adapter_export(self):
        from test_codex_provider import CodexProviderTests
        from codex_client import CodexError
        helper = CodexProviderTests()
        self.addCleanup(helper.doCleanups)
        helper.setUp()
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        ledger = EvidenceLedger(directory, 'codex', 1)
        self.addCleanup(ledger.close, 'failed')
        collector = TaskMetricsCollector(task_id='codex', provider='codex', model='m', evidence=ledger)
        translator = helper.translator()
        translator.set_metrics_collector(collector)
        helper.client.translate.side_effect = CodexError('codex_request_failed', 'private provider message', 502)
        ref = paragraph()
        with self.assertRaises(CodexError):
            translator.llm_translate('source', rate_limit_params={'diagnostic_paragraph_refs': [ref]})
        ledger.close('failed')
        graph = read_evidence(directory, 'codex', 1, 'failed')
        self.assertEqual(graph['requests'][0]['providerCode'], 'codex_request_failed')
        self.assertEqual(graph['requests'][0]['paragraphRefs'], [ref])
        self.assertNotIn('private provider message', json.dumps(graph))

    def test_export_keeps_health_from_the_parent_even_when_last_write_is_lost(self):
        from task_manager import TaskManager, TaskRecord
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        ledger = EvidenceLedger(directory, 'task-one', 3)
        ledger.close('completed')
        manager = TaskManager(directory / 'tasks.json')
        record = TaskRecord('task-one', 'private.pdf', 'openai', ['dual'], {}, directory,
                            status='completed', attempt=3)
        record.progress_detail['evidenceCollection'] = {
            'executionId': ledger.execution_id, 'unflushedEvents': 12, 'queueDropped': 2}
        manager._tasks[record.task_id] = record
        exported = manager.export_diagnostics(record.task_id)
        self.assertEqual(exported['evidence']['taskEvidence'][0]['evidenceStatus'], 'partial')
        self.assertTrue(any(row['findingCode'] == 'evidence_incomplete' for row in exported['analysis']['findings']))

    def test_native_openai_validation_retries_retain_paragraph_and_provider_evidence(self):
        import httpx
        from unittest.mock import Mock
        from test_openai_protocol import ProtocolTests, chat
        from pdf2zh_pro.translator.validation import InvalidTranslation, validate_text
        source = 'This is a complete English paragraph about the experiment.'
        with tempfile.TemporaryDirectory() as directory:
            ledger = EvidenceLedger(directory, 'native', 1)
            self.addCleanup(ledger.close, 'failed')
            translator = ProtocolTests.translator(self, lambda request: httpx.Response(200, json=chat(source)),
                                                 protocol='chat_completions')
            translator.cache = Mock()
            translator.cache.get.return_value = None
            translator.metrics_collector = TaskMetricsCollector(task_id='native', provider='openai', model='test', evidence=ledger)
            ref = paragraph()
            with self.assertRaises(InvalidTranslation):
                translator.llm_translate(source, rate_limit_params={'diagnostic_paragraph_refs': [ref],
                    'validate_output': lambda value: validate_text(source, value, 'zh-CN')})
            ledger.close('incomplete')
            graph = read_evidence(directory, 'native', 1, 'incomplete')
            calls = sorted(graph['requests'], key=lambda row: row['callId'])
            self.assertEqual(len(calls), 2)
            self.assertTrue(all(row['statusCode'] == 200 and row['validationState'] == 'rejected' for row in calls))
            self.assertEqual(calls[1]['parentCallId'], calls[0]['callId'])
            self.assertEqual(calls[1]['retryCause'], 'output_validation')
            self.assertEqual(calls[0]['operationId'], calls[1]['operationId'])
            self.assertEqual(calls[0]['validationFailures'], [{'paragraphRef': ref, 'validationCode': 'unchanged_translation'}])

    def test_old_checkpoint_gets_a_stable_opaque_ref_and_reuse_has_no_request(self):
        from test_translation_recovery import RecoveryTests
        from babeldoc.format.pdf.document_il.midend.il_translator import ParagraphTranslateTracker
        from unittest.mock import Mock
        helper = RecoveryTests()
        with helper.recovery_directory() as directory:
            p, docs, cfg, recovery, translator = helper.fixtures(directory)
            ref = recovery.diagnostic_ref(p)
            self.assertRegex(ref, r'^p-[0-9a-f]{32}$')
            translator.translate_paragraph(p, docs.page[0], Mock(), ParagraphTranslateTracker(), {}, {})
            recovery.finish()
            # Simulate a checkpoint created before opaque diagnostic refs existed.
            with recovery.connection:
                for key, payload in recovery.connection.execute('SELECT key,payload FROM paragraphs').fetchall():
                    row = json.loads(payload)
                    row.pop('paragraphRef', None)
                    recovery.connection.execute('UPDATE paragraphs SET payload=? WHERE key=?', (json.dumps(row), key))
            p2, docs2, cfg2, resumed, t2 = helper.fixtures(directory)
            ref = resumed.diagnostic_ref(p2)
            # The ref commits during registration, even if cancelled before reuse.
            p3, _, _, registered_again, _ = helper.fixtures(directory)
            self.assertEqual(registered_again.diagnostic_ref(p3), ref)
            ledger = EvidenceLedger(directory, 'native', 2)
            resumed.evidence = ledger
            t2.translate_paragraph(p2, docs2.page[0], Mock(), ParagraphTranslateTracker(), {}, {})
            resumed.finish()
            ledger.close('completed')
            graph = read_evidence(directory, 'native', 2, 'completed')
            self.assertEqual(graph['requests'], [])
            self.assertEqual(graph['paragraphs'][0]['outcomeSource'], 'checkpoint')
            self.assertEqual(graph['paragraphs'][0]['paragraphRef'], ref)
            t2.translate_engine.llm_translate.assert_not_called()

    def test_native_empty_output_is_not_a_provider_diagnosis(self):
        import httpx
        from unittest.mock import Mock
        from test_openai_protocol import ProtocolTests, chat
        from pdf2zh_pro.translator.validation import InvalidTranslation
        with tempfile.TemporaryDirectory() as directory:
            ledger = EvidenceLedger(directory, 'native', 1)
            self.addCleanup(ledger.close, 'failed')
            translator = ProtocolTests.translator(self, lambda request: httpx.Response(200, json=chat('')),
                                                 protocol='chat_completions')
            translator.cache = Mock()
            translator.cache.get.return_value = None
            translator.metrics_collector = TaskMetricsCollector(task_id='native', provider='openai', model='test', evidence=ledger)
            with self.assertRaises(InvalidTranslation):
                translator.llm_translate('test', rate_limit_params={'diagnostic_paragraph_refs': [paragraph()]})
            ledger.close('incomplete')
            graph = read_evidence(directory, 'native', 1, 'incomplete')
            codes = {row['findingCode'] for row in analyze([{'taskId': 'native'}], {'taskEvidence': [graph]})['findings']}
            self.assertIn('validation_failed', codes)
            self.assertNotIn('provider_request_failed', codes)

    def test_native_partial_batch_links_only_the_failed_paragraph_to_fallback(self):
        from contextlib import ExitStack
        import httpx
        from test_openai_protocol import ProtocolTests, chat
        from test_partial_batch import PartialBatchTests, MemoryCache
        with ExitStack() as stack:
            directory = stack.enter_context(tempfile.TemporaryDirectory())
            paragraphs, recovery, fixture_engine, batch, run = PartialBatchTests().fixture(
                stack, directory, PartialBatchTests.partial_response)
            ledger = EvidenceLedger(directory, 'native', 1)
            stack.callback(ledger.close, 'failed')
            recovery.evidence = ledger
            engine = ProtocolTests.translator(self,
                lambda request: httpx.Response(200, json=chat(PartialBatchTests.partial_response(
                    json.loads(request.content)['messages'][0]['content']))), protocol='chat_completions')
            engine.cache = MemoryCache()
            engine.metrics_collector = TaskMetricsCollector(task_id='native', provider='openai', model='test', evidence=ledger)
            batch.translate_engine = batch.il_translator.translate_engine = engine
            run()
            ledger.close('completed')
            graph = read_evidence(directory, 'native', 1, 'completed')
            self.assertEqual(len(graph['requests']), 2)
            parent = next(row for row in graph['requests'] if row['batchSize'] == 6)
            child = next(row for row in graph['requests'] if row['batchSize'] == 1)
            self.assertEqual(len(parent['paragraphRefs']), 6)
            self.assertEqual(child['paragraphRefs'], [recovery.diagnostic_ref(paragraphs[2])])
            self.assertEqual(child['parentCallId'], parent['callId'])
            self.assertEqual(child['retryCause'], 'batch_partial')
            self.assertEqual(child['validationState'], 'accepted')
            self.assertEqual(parent['validationFailures'][0]['paragraphRef'], child['paragraphRefs'][0])
            self.assertTrue(all(row['paragraphState'] == 'succeeded' for row in graph['paragraphs']))


if __name__ == '__main__':
    unittest.main()
