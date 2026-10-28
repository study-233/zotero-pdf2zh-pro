"""401 recovery must preserve account binding, request budgets and accounting."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import httpx

from chatgpt_auth import AuthStore, private_write
from chatgpt_client import ChatGPTClient, ChatGPTError
from observability import TaskMetricsCollector
from pdf2zh_pro.translator.chatgpt import ChatGPTTranslator
from pdf2zh_pro.pdf import create_runtime_settings
from test_chatgpt import credential, Events, completed
from test_pdf2zh_next_service import make_settings_payload
from text_translation import TextTranslationError


class UnauthorizedRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        record = credential()
        record['expires_at'] = time.time() + 3600
        private_write(self.directory / 'credentials.json', record)
        self.exchanges, self.requests = [], []
        self.store = AuthStore(self.directory, client=httpx.Client(transport=httpx.MockTransport(self.refresh)))
        self.addCleanup(self.store.client.close)

    def refresh(self, request):
        self.exchanges.append(request)
        return httpx.Response(200, json={'access_token': 'renewed', 'refresh_token': 'rotated',
                                       'expires_in': 3600, 'token_type': 'Bearer'})

    def recovered(self, request):
        self.requests.append(request)
        if request.headers['Authorization'] == 'Bearer old-access':
            return httpx.Response(401, json={'error': {}})
        if request.method == 'GET':
            return httpx.Response(200, json={'models': [{'slug': 'm', 'visibility': 'list'}]})
        return httpx.Response(200, stream=Events([completed()]))

    def client(self, handler=None):
        client = ChatGPTClient(self.directory, store=self.store, transport=httpx.MockTransport(handler or self.recovered))
        self.addCleanup(client.close)
        return client

    def generate(self, client, **kwargs):
        return client.translate('source', model='m', reasoning_effort=None, timeout=5, **kwargs)

    def summary(self, client):
        return client.usage.summary(self.store.status()['account_fingerprint'])

    def test_401_refreshes_once_and_records_two_attempts_without_doubling_tokens(self):
        client = self.client()
        starts, rejected, usage = Mock(), Mock(), []
        starts.side_effect = ['first', 'second']
        self.assertEqual(self.generate(client, on_request_start=starts, on_auth_retry=rejected,
                                       on_usage=usage.append).text, '准确译文')
        self.assertEqual((len(self.requests), len(self.exchanges), starts.call_count, rejected.call_count), (2, 1, 2, 1))
        self.assertEqual(json.loads(self.requests[0].content), json.loads(self.requests[1].content))
        state = self.summary(client)
        self.assertEqual((state['calls'], state['succeeded'], state['unknownUsageCalls']), (2, 1, 1))
        self.assertEqual((state['inputTokens'], state['outputTokens'], state['cachedInputTokens']), (15, 6, 3))
        self.assertEqual(len(usage), 1)
        self.assertFalse(self.store.status()['requires_login'])
        with client.usage.connect() as db:
            self.assertEqual(db.execute('SELECT id,status,input FROM calls ORDER BY started').fetchall(),
                             [('first', 'error', None), ('second', 'ok', 15)])

    def test_model_catalog_recovers_without_counting_generation(self):
        client = self.client()
        self.assertEqual(client.model_catalog()[0]['id'], 'm')
        self.assertEqual((len(self.requests), len(self.exchanges), self.summary(client)['calls']), (2, 1, 0))
        client.model_catalog()
        self.assertEqual(len(self.requests), 2)

    def test_second_401_stops_and_persists_relogin(self):
        handler = Mock(return_value=httpx.Response(401, json={'error': {}}))
        client = self.client(handler)
        with self.assertRaises(ChatGPTError) as caught:
            self.generate(client)
        self.assertEqual(caught.exception.code, 'chatgpt_sign_in_required')
        self.assertEqual((handler.call_count, len(self.exchanges)), (2, 1))
        self.assertTrue(AuthStore(self.directory).status()['requires_login'])
        self.assertEqual(self.summary(client)['unknownUsageCalls'], 2)
        with self.assertRaises(ChatGPTError):
            self.generate(client)
        self.assertEqual(handler.call_count, 2)

    def test_rejected_refresh_stops_but_transient_refresh_keeps_login(self):
        for status in (400, 503):
            with self.subTest(status=status):
                record = credential(); record['expires_at'] = time.time() + 3600
                private_write(self.store.record_path, record)
                handler = Mock(return_value=httpx.Response(401, json={}))
                client = self.client(handler)
                with patch.object(self.store.client, 'post', return_value=httpx.Response(status,
                        request=httpx.Request('POST', 'https://example.invalid/token'), json={'error': 'invalid_grant'}
                        if status == 400 else {'error': 'server_error'})):
                    with self.assertRaises(ChatGPTError) as caught: self.generate(client)
                self.assertEqual(handler.call_count, 1)
                self.assertEqual(caught.exception.retryable, status == 503)
                self.assertEqual(self.store.status()['requires_login'], status == 400)

    def test_stream_auth_error_after_output_is_never_replayed(self):
        failure = {'type': 'response.failed', 'response': {'usage': {'input_tokens': 8, 'output_tokens': 2},
                   'error': {'code': 'subscription_sharing_invalid_user'}}}
        handler = Mock(return_value=httpx.Response(200, stream=Events([
            {'type': 'response.output_text.delta', 'delta': '部分译文'}, failure])))
        client = self.client(handler); chunks = []
        with self.assertRaises(ChatGPTError): self.generate(client, on_text=chunks.append)
        self.assertEqual((handler.call_count, len(self.exchanges), chunks), (1, 0, ['部分译文']))
        state = self.summary(client)
        self.assertEqual((state['calls'], state['inputTokens'], state['outputTokens']), (1, 8, 2))

    def test_concurrent_401_reuses_rotated_token_and_refreshes_once(self):
        barrier = threading.Barrier(2)
        def handle(request):
            if request.headers['Authorization'] == 'Bearer old-access': barrier.wait(timeout=3)
            return self.recovered(request)
        clients = [self.client(handle), self.client(handle)]
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(self.generate, clients))
        self.assertEqual([r.text for r in results], ['准确译文'] * 2)
        self.assertEqual((len(self.requests), len(self.exchanges)), (4, 1))
        state = self.summary(clients[0])
        self.assertEqual((state['calls'], state['inputTokens'], state['unknownUsageCalls']), (4, 30, 2))

    def test_account_switch_at_401_does_not_refresh_or_send_as_new_account(self):
        def handle(request):
            record = credential(); record.update(subject='different-account', expires_at=time.time() + 3600)
            private_write(self.store.record_path, record)
            return httpx.Response(401, json={})
        handler = Mock(side_effect=handle)
        with self.assertRaises(ChatGPTError) as caught: self.generate(self.client(handler))
        self.assertEqual(caught.exception.code, 'chatgpt_account_changed')
        self.assertEqual((handler.call_count, len(self.exchanges)), (1, 0))
        self.assertFalse(self.store.status()['requires_login'])

    def test_account_binding_is_checked_inside_refresh_lock(self):
        fingerprint = self.store.status()['account_fingerprint']
        record = credential(); record['subject'] = 'different-account'
        private_write(self.store.record_path, record)
        from chatgpt_auth import AuthError
        with self.assertRaisesRegex(AuthError, 'chatgpt_account_changed'):
            self.store.access_token(rejected_token='old-access', expected_account=fingerprint)
        self.assertEqual(self.exchanges, [])

    def test_cancel_after_refresh_prevents_second_generation(self):
        def check():
            if self.exchanges: raise TextTranslationError('selection_cancelled', 499)
        client = self.client()
        with self.assertRaises(TextTranslationError): self.generate(client, check_cancelled=check)
        self.assertEqual((len(self.requests), len(self.exchanges), self.summary(client)['calls']), (1, 1, 1))
        self.assertTrue(self.store.status()['authenticated'])

    def test_formal_model_measurement_can_disable_retry(self):
        client = self.client()
        with self.assertRaises(ChatGPTError): self.generate(client, allow_auth_retry=False)
        self.assertEqual((len(self.requests), len(self.exchanges), self.summary(client)['calls']), (1, 0, 1))
        # A first rejection alone is not proof that refresh or a new login is needed.
        self.assertFalse(self.store.status()['requires_login'])

    def adapter(self, client, collector):
        settings = create_runtime_settings(make_settings_payload(service='chatgpt', llm_api={
            'model': 'm', 'reasoningEffort': 'low'}))
        with patch('pdf2zh_pro.translator.chatgpt.get_chatgpt_client', return_value=client), \
                patch.object(client, 'check_ready', return_value={'reasoningEffort': 'low'}):
            adapter = ChatGPTTranslator(settings, None)
        adapter.set_metrics_collector(collector)
        adapter.rate_limiter = Mock()
        return adapter

    def test_pdf_retry_reconciles_metrics_parent_ids_rate_limits_and_budget(self):
        client = self.client(); events = []
        collector = TaskMetricsCollector(task_id='refresh', provider='chatgpt', model='m', journal=events.append)
        adapter = self.adapter(client, collector)
        budget, on_attempt = Mock(), Mock()
        self.assertEqual(adapter._request('source', {'request_budget': budget, 'on_attempt': on_attempt,
            'metric_operation_id': 'paragraphs', 'paragraph_ids': ['p1', 'p2']}), '准确译文')
        self.assertEqual((budget.reserve.call_count, on_attempt.call_count, adapter.rate_limiter.wait.call_count), (2, 2, 2))
        stats = collector.snapshot()
        self.assertEqual(tuple(stats['requests'][key] for key in ('attempts', 'succeeded', 'failed', 'retries', 'active')), (2, 1, 1, 1, 0))
        self.assertEqual((stats['tokens']['input'], stats['tokens']['output']), (15, 6))
        starts = [event for event in events if event['event'] == 'request_started']
        finishes = [event for event in events if event['event'] == 'request_finished']
        self.assertEqual(len(finishes), 2)
        self.assertEqual(starts[1]['parentCallId'], starts[0]['callId'])
        self.assertEqual(starts[1]['retryCause'], 'auth_refresh')
        self.assertEqual(starts[1]['paragraphIds'], ['p1', 'p2'])
        with client.usage.connect() as db:
            self.assertEqual({r[0] for r in db.execute('SELECT id FROM calls')}, {e['callId'] for e in starts})
        adapter.request_state.check()

    def test_pdf_retry_budget_exhaustion_leaves_no_phantom_active_request(self):
        from pdf2zh_pro.translator.request_budget import TranslationBudgetExhausted
        client = self.client()
        collector = TaskMetricsCollector(task_id='limited', provider='chatgpt', model='m')
        adapter = self.adapter(client, collector)
        budget = Mock(); budget.reserve.side_effect = [None, TranslationBudgetExhausted()]
        with self.assertRaises(TranslationBudgetExhausted): adapter._request('source', {'request_budget': budget})
        stats = collector.snapshot()['requests']
        self.assertEqual((len(self.requests), stats['attempts'], stats['active'], stats['failed']), (1, 1, 0, 1))
        self.assertEqual(self.summary(client)['calls'], 1)


if __name__ == '__main__':
    unittest.main()
