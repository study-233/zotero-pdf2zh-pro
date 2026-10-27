"""Offline contracts for the public ChatGPT plan integration."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import httpx
from flask import Flask

from chatgpt_auth import AuthStore, AuthError, private_write
from chatgpt_client import ChatGPTClient, ChatGPTError, response_error
from chatgpt_routes import install_chatgpt_routes
from chatgpt_usage import UsageStore
from pdf2zh_next_service import create_runtime_settings
from pdf2zh_next.translator.translator_impl.chatgpt import ChatGPTTranslator
from pdf2zh_next.translator import get_translator, get_term_translator
from observability import TaskMetricsCollector
from test_pdf2zh_next_service import make_settings_payload
from text_translation import TextTranslationError


ACCOUNT = 'a' * 64


def credential(**changes):
    return dict(client_id='owned-client', issuer='https://auth.openai.com', subject='subject',
                email='reader@example.invalid', access_token='old-access', refresh_token='old-refresh',
                scopes=['chatgpt.tokens.use.direct'], expires_at=time.time()-10, **changes)


class Events(httpx.AsyncByteStream):
    def __init__(self, events, delay=0): self.events, self.delay, self.closed = events, delay, False
    async def __aiter__(self):
        for event in self.events:
            yield ('data: ' + json.dumps(event) + '\n\n').encode()
        if self.delay: await asyncio.sleep(self.delay)
    async def aclose(self): self.closed = True


def completed():
    return {'type': 'response.completed', 'sequence_number': 2, 'response': {
        'status': 'completed', 'usage': {'input_tokens': 15, 'output_tokens': 6,
        'input_tokens_details': {'cached_tokens': 3}, 'output_tokens_details': {'reasoning_tokens': 0}},
        'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': '准确译文'}]}]}}


class ChatGPTContracts(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.store = Mock()
        self.store.status.return_value = {'authenticated': True, 'plan_usage_authorized': True, 'account_fingerprint': ACCOUNT}
        self.store.access_token.return_value = 'private-access-token'

    def client(self, handler):
        client = ChatGPTClient(self.directory, store=self.store, transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)
        return client

    def generate(self, client, **kwargs):
        return client.translate('source', model='gpt-6-luna', reasoning_effort='low', timeout=2, **kwargs)

    def test_stream_deduplicates_usage_and_deltas_and_persists_actual_call(self):
        delta = {'type': 'response.output_text.delta', 'sequence_number': 1, 'delta': '准确译文'}
        stream = Events([delta, delta, completed(), completed()])
        def handle(request):
            body = json.loads(request.content)
            self.assertFalse(body['store']); self.assertTrue(body['stream'])
            self.assertEqual(body['reasoning']['effort'], 'low')
            self.assertNotIn('temperature', body)
            return httpx.Response(200, stream=stream)
        client = self.client(handle); seen = []
        result = self.generate(client, on_text=seen.append)
        self.assertEqual(seen, ['准确译文'])
        self.assertEqual(result.usage['completion_tokens'], 6)
        state = UsageStore(self.directory).summary(ACCOUNT)
        self.assertEqual((state['calls'], state['inputTokens'], state['completeCalls']), (1, 15, 1))
        self.assertTrue(stream.closed)
        self.assertNotIn('private-access-token', (self.directory/'usage.sqlite3').read_bytes().decode(errors='ignore'))

    def test_pdf_initialization_and_translation_inside_an_existing_event_loop(self):
        def handle(request):
            if request.method == 'GET':
                return httpx.Response(200, json={'models': [{'slug':'gpt-6-luna','visibility':'list',
                    'supported_reasoning_levels':[{'effort':'low'}], 'default_reasoning_level':'low'}]})
            return httpx.Response(200, stream=Events([completed()]))
        client = self.client(handle)
        async def pdf_worker():
            self.assertEqual(client.check_ready('gpt-6-luna', 'low')['protocol'], 'responses')
            return self.generate(client)
        self.assertEqual(asyncio.run(pdf_worker()).text, '准确译文')
        self.assertEqual(client.usage.summary(ACCOUNT)['calls'], 1)

    def test_automatic_low_uses_account_capabilities_and_keeps_explicit_choices(self):
        client = self.client(lambda r: httpx.Response(200))
        with patch.object(client, 'model_catalog', return_value=[{'id':'gpt-6-luna',
                'supportedReasoningEfforts':['low', 'medium'], 'defaultReasoningEffort':'medium'}]):
            self.assertEqual(client.check_ready('gpt-6-luna', 'auto')['reasoningEffort'], 'low')
            self.assertEqual(client.check_ready('gpt-6-luna')['reasoningEffort'], 'medium')
        with patch.object(client, 'model_catalog', return_value=[{'id':'gpt-6-luna',
                'supportedReasoningEfforts':['medium'], 'defaultReasoningEffort':'medium'}]):
            self.assertEqual(client.check_ready('gpt-6-luna', 'auto')['reasoningEffort'], 'medium')

    def test_failure_after_partial_output_keeps_known_usage_and_stops_task(self):
        failure = {'type': 'response.failed', 'response': {'usage': {'input_tokens': 8, 'output_tokens': 2},
            'error': {'code': 'subscription_sharing_usage_limit_exceeded'}}}
        client = self.client(lambda r: httpx.Response(200, stream=Events([
            {'type': 'response.output_text.delta', 'delta': '部分'}, failure])))
        with self.assertRaises(ChatGPTError) as caught: self.generate(client)
        self.assertTrue(caught.exception.stop_task)
        self.assertFalse(caught.exception.retryable)
        self.assertEqual(caught.exception.code, 'chatgpt_usage_limit')
        state = client.usage.summary(ACCOUNT)
        self.assertEqual((state['inputTokens'], state['outputTokens'], state['unknownUsageCalls']), (8, 2, 1))

    def test_cancel_while_waiting_for_response_headers_is_bounded(self):
        entered, exited = threading.Event(), threading.Event()
        async def handle(request):
            entered.set()
            try: await asyncio.sleep(30)
            finally: exited.set()
        client = self.client(handle)
        def cancelled():
            if entered.is_set(): raise TextTranslationError('selection_cancelled', 499)
        started = time.monotonic()
        with self.assertRaises(TextTranslationError): self.generate(client, check_cancelled=cancelled)
        self.assertLess(time.monotonic()-started, 1)
        self.assertTrue(exited.is_set())
        state = client.usage.summary(ACCOUNT)
        self.assertEqual(state['unknownUsageCalls'], 1)
        self.assertIsNone(state['inputTokens'])

    def test_idle_stream_timeout_closes_connection_and_retains_unknown(self):
        stream = Events([], delay=30)
        client = self.client(lambda r: httpx.Response(200, stream=stream))
        with self.assertRaises(ChatGPTError) as caught:
            client.translate('source', model='m', reasoning_effort=None, timeout=.15)
        self.assertEqual(caught.exception.code, 'chatgpt_timeout')
        self.assertTrue(stream.closed)
        self.assertIsNone(client.usage.summary(ACCOUNT)['outputTokens'])

    def test_account_switch_and_revocation_do_not_send_generation(self):
        handler = Mock()
        client = self.client(handler); client.account_id = 'b'*64
        with self.assertRaises(ChatGPTError) as caught: self.generate(client)
        self.assertEqual(caught.exception.code, 'chatgpt_account_changed')
        self.assertEqual(client.usage.summary(ACCOUNT)['calls'], 0)
        handler.assert_not_called()
        admission = Mock()
        with self.assertRaises(ChatGPTError): self.generate(client, on_request_start=admission)
        admission.assert_not_called()

    def test_terminal_errors_do_not_retry_while_network_errors_can(self):
        for status in (400, 401, 403, 404):
            error = response_error(status, {'detail': 'private provider response'})
            self.assertTrue(error.stop_task)
            self.assertFalse(error.retryable)
            self.assertNotIn('private provider response', str(error))
        self.assertTrue(response_error(503, {}).retryable)
        self.assertTrue(response_error(429, {}).retryable)

    def test_usage_survives_restart_and_duplicate_completion_does_not_double_count(self):
        usage = UsageStore(self.directory)
        usage.start('finished', ACCOUNT, 'm', 'translation')
        usage.start('killed', ACCOUNT, 'm', 'translation')
        for _ in range(2): usage.finish('finished', 'ok', {'prompt_tokens': 15, 'completion_tokens': 6}, True)
        state = UsageStore(self.directory).summary(ACCOUNT)
        self.assertEqual((state['calls'], state['inputTokens'], state['unknownUsageCalls']), (2, 15, 1))
        self.assertIsNone(state['quotaPercent'])

    def test_concurrent_expired_refresh_rotates_only_once_and_atomic_record_survives(self):
        private_write(self.directory/'credentials.json', credential())
        calls = []
        def response(request):
            calls.append(request)
            return httpx.Response(200, json={'access_token':'new-access','refresh_token':'new-refresh',
                                           'token_type':'Bearer','expires_in':3600})
        stores = [AuthStore(self.directory, client=httpx.Client(transport=httpx.MockTransport(response))) for _ in range(2)]
        results = []
        threads = [threading.Thread(target=lambda s=s: results.append(s.access_token())) for s in stores]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(results, ['new-access']*2); self.assertEqual(len(calls), 1)
        self.assertEqual(stores[0].read()['refresh_token'], 'new-refresh')
        if os.name != 'nt':
            self.assertFalse((self.directory/'credentials.json').stat().st_mode & 0o077)
        self.assertNotIn('access_token', stores[0].status())

    def test_logout_revoke_failure_clears_only_session_and_preserves_registration(self):
        private_write(self.directory/'credentials.json', credential())
        private_write(self.directory/'registration.json', {'client_id':'owned-client'})
        store = AuthStore(self.directory, client=httpx.Client(transport=httpx.MockTransport(
            lambda r: httpx.Response(503, text='not json'))))
        self.assertFalse(store.logout()['revocationConfirmed'])
        self.assertFalse(store.record_path.exists())
        self.assertEqual(json.loads((self.directory/'registration.json').read_text())['client_id'], 'owned-client')

    def test_account_routes_reject_browser_origins_and_rebinding(self):
        app = Flask(__name__); install_chatgpt_routes(app, lambda: False, self.directory)
        client = app.test_client(); headers = {'X-PDF2ZH-Client':'zotero'}
        self.assertEqual(client.get('/chatgpt/status').status_code, 403)
        self.assertEqual(client.get('/chatgpt/status', headers={**headers,'Origin':'https://evil.invalid'}).status_code, 403)
        self.assertEqual(client.get('/chatgpt/status', headers={**headers,'Host':'evil.invalid'}).status_code, 403)
        state = client.get('/chatgpt/status', headers=headers)
        self.assertEqual(state.status_code, 200)
        self.assertFalse(state.json['account']['authenticated'])

    def test_login_cancel_reports_progress_preserves_account_and_allows_retry(self):
        private_write(self.directory/'credentials.json', credential())
        app = Flask(__name__); install_chatgpt_routes(app, lambda: False, self.directory)
        client = app.test_client(); headers = {'X-PDF2ZH-Client': 'zotero'}
        entered, finish = threading.Event(), threading.Event()
        callbacks = []
        def waiting_login(directory, *, ready, cancelled, new_account):
            callbacks.append(ready)
            ready('http://127.0.0.1:12345/start/test')
            entered.set()
            finish.wait(3)
            return {'authenticated': False, 'error': 'oauth_login_cancelled'}
        with patch('chatgpt_routes.login', side_effect=waiting_login), patch('chatgpt_routes.close_chatgpt_clients'):
            session = app.extensions['chatgpt_login']
            for _ in range(2):
                entered.clear(); finish.clear()
                try:
                    self.assertEqual(client.post('/chatgpt/login', json={}, headers=headers).status_code, 200)
                    self.assertTrue(entered.wait(1))
                    response = client.post('/chatgpt/login/cancel', json={}, headers=headers)
                    self.assertEqual(response.json['login']['status'], 'cancelling')
                    # A late ready callback must not resurrect the cancelled URL.
                    callbacks[-1]('http://127.0.0.1:12345/start/late')
                    state = client.get('/chatgpt/status', headers=headers).json
                    self.assertEqual(state['login'], {'status': 'cancelling'})
                    self.assertTrue(state['account']['authenticated'])
                finally:
                    finish.set()
                    session.thread.join(2)
                self.assertFalse(session.thread.is_alive())
                self.assertEqual(session.snapshot()['status'], 'cancelled')
                self.assertEqual(client.post('/chatgpt/login/cancel', json={}, headers=headers).json['login']['status'], 'cancelled')
            self.assertEqual(len(callbacks), 2)

    def test_pdf_adapter_shares_task_stop_state_and_records_responses_usage(self):
        factory = Mock()
        factory.check_ready.return_value = {'reasoningEffort':'low'}
        def translate(*args, **kwargs):
            kwargs['on_request_start']()
            return Mock(text='正文译文', usage={'prompt_tokens':20,'completion_tokens':8})
        factory.translate.side_effect = translate
        payload = make_settings_payload(service='chatgpt', llm_api={'model':'gpt-6-luna','reasoningEffort':'low'})
        settings = create_runtime_settings(payload)
        with patch('pdf2zh_next.translator.translator_impl.chatgpt.get_chatgpt_client', return_value=factory):
            adapter = ChatGPTTranslator(settings, None)
            collector = TaskMetricsCollector(task_id='test', provider='chatgpt', model='gpt-6-luna')
            adapter.set_metrics_collector(collector)
            self.assertEqual(adapter.llm_translate('source', ignore_cache=True), '正文译文')
            self.assertEqual(collector.snapshot()['tokens']['input'], 20)
            factory.translate.side_effect = response_error(401, {})
            with self.assertRaises(ChatGPTError): adapter.llm_translate('next', ignore_cache=True)
            with self.assertRaises(ChatGPTError): adapter.llm_translate('never send', ignore_cache=True)
            self.assertEqual(factory.translate.call_count, 2)

    def test_pdf_default_24_workers_and_three_qps_preserve_lower_user_limits(self):
        client = Mock()
        client.check_ready.return_value = {'reasoningEffort':'low'}
        with patch('pdf2zh_next.translator.translator_impl.chatgpt.get_chatgpt_client', return_value=client):
            for requested, expected in ((None,24), (0,24), (50,24), (24,24), (8,8), (2,2), (1,1)):
                for requested_qps, expected_qps in ((10,3), (3,3), (2,2), (1,1)):
                    with self.subTest(requested=requested, qps=requested_qps):
                        settings = create_runtime_settings(make_settings_payload(service='chatgpt',
                            pool_size=requested, qps=requested_qps, no_auto_extract_glossary=False,
                            llm_api={'model':'gpt-6-luna','reasoningEffort':'low'}))
                        main = get_translator(settings)
                        self.assertEqual(settings.translation.pool_max_workers, expected)
                        self.assertEqual(main.rate_limiter.max_qps, expected_qps)
                        terms = get_term_translator(settings)
                        self.assertEqual(settings.translation.term_pool_max_workers, expected)
                        self.assertEqual(terms.rate_limiter.max_qps, expected_qps)
                        settings.translation.term_pool_max_workers = 1
                        settings.translation.term_qps = 1
                        terms = get_term_translator(settings)
                        self.assertEqual(settings.translation.term_pool_max_workers, 1)
                        self.assertEqual(terms.rate_limiter.max_qps, 1)
                        main.configure_execution(expected, lambda: None)
                        for _ in range(expected):
                            self.assertTrue(main._request_slots.acquire(blocking=False))
                        self.assertFalse(main._request_slots.acquire(blocking=False))
                        for _ in range(expected): main._request_slots.release()

    def test_24_live_requests_and_queued_cancel_reconcile_usage(self):
        client, started, release, cancelled = Mock(), threading.Barrier(25), threading.Event(), threading.Event()
        def translate(prompt, **options):
            options['on_request_start']()
            started.wait(5)
            if not release.wait(5): raise TimeoutError('offline request was not released')
            return Mock(text='译文', usage={'prompt_tokens':15,'completion_tokens':6,'cache_hit_tokens':0})
        client.translate.side_effect = translate
        settings = create_runtime_settings(make_settings_payload(service='chatgpt', llm_api={'model':'gpt-6-luna'}))
        with patch('pdf2zh_next.translator.translator_impl.chatgpt.get_chatgpt_client', return_value=client):
            adapter = ChatGPTTranslator(settings, None)
        # No test override or supplied semaphore: the product default admits 24.
        adapter.configure_execution(50, lambda: None)
        collector = TaskMetricsCollector(task_id='24-workers', provider='chatgpt', model='gpt-6-luna')
        adapter.set_metrics_collector(collector)
        with ThreadPoolExecutor(max_workers=25) as workers:
            calls = [workers.submit(adapter._request, 'source') for _ in range(24)]
            try:
                started.wait(5)
                self.assertEqual(collector.snapshot()['requests']['active'], 24)
                def check():
                    if cancelled.is_set(): raise asyncio.CancelledError()
                queued = workers.submit(adapter._request, 'cancelled source', {'check_cancelled':check})
                cancelled.set()
                with self.assertRaises(asyncio.CancelledError): queued.result(timeout=2)
                self.assertEqual(client.translate.call_count, 24)
            finally:
                release.set()
            self.assertEqual([call.result(timeout=2) for call in calls], ['译文']*24)
        stats = collector.snapshot()
        self.assertEqual(tuple(stats['requests'][k] for k in ('attempts','succeeded','failed','active')), (24,24,0,0))
        self.assertEqual((stats['tokens']['input'],stats['tokens']['output']), (360,144))


if __name__ == '__main__': unittest.main()

class LoginExpiryTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        private_write(self.directory / 'credentials.json', credential())

    def store(self, handler):
        return AuthStore(self.directory, client=httpx.Client(transport=httpx.MockTransport(handler)))

    def test_expired_access_refreshes_silently_and_status_stays_signed_in(self):
        store = self.store(lambda r: httpx.Response(200, json={
            'access_token': 'renewed', 'refresh_token': 'rotated', 'expires_in': 3600, 'token_type': 'Bearer'}))
        self.assertTrue(store.status()['authenticated'])
        client = ChatGPTClient(self.directory, store=store, transport=httpx.MockTransport(
            lambda r: httpx.Response(200, stream=Events([completed()]))))
        self.assertEqual(client.translate('source', model='m', reasoning_effort=None, timeout=2).text, '准确译文')
        self.assertFalse(store.status()['requires_login'])
        self.assertTrue(store.status()['authenticated'])
        self.assertEqual(client.usage.summary(store.status()['account_fingerprint'])['calls'], 1)

    def test_revoked_refresh_persists_failure_without_sending_generation(self):
        store = self.store(lambda r: httpx.Response(400, json={'error': 'invalid_grant'}))
        handler = Mock()
        client = ChatGPTClient(self.directory, store=store, transport=httpx.MockTransport(handler))
        for _ in range(2):
            with self.assertRaises(ChatGPTError) as caught:
                client.translate('source', model='m', reasoning_effort=None, timeout=2)
            self.assertEqual(str(caught.exception), 'ChatGPT 登录已失效，请重新登录。')
        handler.assert_not_called()
        restarted = AuthStore(self.directory)
        self.assertTrue(restarted.status()['requires_login'])
        self.assertFalse(restarted.status()['authenticated'])
        self.assertNotIn('old-refresh', json.dumps(restarted.status()))
        app = Flask(__name__)
        install_chatgpt_routes(app, lambda: False, self.directory)
        state = app.test_client().get('/chatgpt/status', headers={'X-PDF2ZH-Client': 'zotero'}).json
        self.assertTrue(state['account']['requires_login'])

    def test_network_and_service_failures_do_not_invalidate_login(self):
        for status in (429, 500, 503):
            with self.subTest(status=status):
                store = self.store(lambda r: httpx.Response(status, text='temporary failure'))
                client = ChatGPTClient(self.directory, store=store)
                with self.assertRaises(ChatGPTError) as caught: client._token()
                self.assertTrue(caught.exception.retryable)
                self.assertEqual(caught.exception.code, 'chatgpt_unavailable')
                self.assertTrue(store.status()['authenticated'])
                self.assertFalse(store.status()['requires_login'])

    def test_unauthorized_catalog_and_stream_mark_login_expired(self):
        for mode in ('catalog', 'http', 'stream'):
            with self.subTest(mode=mode):
                record = credential(); record['expires_at'] = time.time() + 3600
                private_write(self.directory / 'credentials.json', record)
                store = self.store(lambda r: httpx.Response(200, json={
                    'access_token': 'renewed', 'refresh_token': 'rotated', 'expires_in': 3600, 'token_type': 'Bearer'}))
                def handler(request):
                    if mode == 'stream':
                        return httpx.Response(200, stream=Events([{'type': 'response.failed', 'response': {
                            'error': {'code': 'subscription_sharing_invalid_user'}}}]))
                    return httpx.Response(401, json={'error': {'message': 'private'}})
                client = ChatGPTClient(self.directory, store=store, transport=httpx.MockTransport(handler))
                with self.assertRaises(ChatGPTError) as caught:
                    if mode == 'catalog': client.model_catalog()
                    else: client.translate('source', model='m', reasoning_effort=None, timeout=2)
                self.assertEqual(caught.exception.code, 'chatgpt_sign_in_required')
                self.assertTrue(store.status()['requires_login'])

    def test_late_unauthorized_cannot_invalidate_replaced_credentials(self):
        store = self.store(Mock())
        store.mark_sign_in_required('old-access')
        self.assertTrue(store.status()['requires_login'])
        record = credential(); record.update(access_token='new-login', expires_at=time.time()+3600)
        private_write(store.record_path, record)
        store.mark_sign_in_required('old-access')
        self.assertTrue(store.status()['authenticated'])
        self.assertFalse(store.status()['requires_login'])

    def test_pdf_diagnostics_preserve_login_reason_and_exclude_transient_refresh(self):
        from pdf2zh_next_service import diagnose_service_error
        for message in ('ChatGPT 登录已失效，请重新登录。', 'ChatGPT 授权无法续期，请重新登录。', 'chatgpt_sign_in_required'):
            self.assertEqual(diagnose_service_error(message)[0]['code'], 'chatgpt_sign_in_required')
        self.assertNotIn('chatgpt_sign_in_required', [d['code'] for d in diagnose_service_error(
            '暂时无法续期 ChatGPT 授权，请检查网络后重试。')])
