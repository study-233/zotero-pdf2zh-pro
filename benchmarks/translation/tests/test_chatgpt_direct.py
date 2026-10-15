import asyncio
import json
import contextlib
import io
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import httpx

from benchmarks.translation.chatgpt_auth import (
    AUTHORITY, AuthError, AuthStore, b64, private_write, verify_identity,
)
from benchmarks.translation.chatgpt_direct import DirectProvider
from benchmarks.translation import direct_compare
from benchmarks.translation.common import code_identity, rows, read


class AuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from cryptography.hazmat.primitives.asymmetric import rsa
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public = cls.key.public_key().public_numbers()
        cls.jwks = {'keys': [{'kid': 'test', 'kty': 'RSA', 'alg': 'RS256',
                              'e': b64(public.e.to_bytes(3, 'big')),
                              'n': b64(public.n.to_bytes(256, 'big'))}]}

    def token(self, **changes):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        claims = dict(iss=AUTHORITY, aud='oaiapp_test', sub='test-user', nonce='nonce', iat=100, exp=200)
        claims.update(changes)
        prefix = b64(json.dumps({'alg': 'RS256', 'kid': 'test'}).encode()) + '.' + b64(json.dumps(claims).encode())
        return prefix + '.' + b64(self.key.sign(prefix.encode(), padding.PKCS1v15(), hashes.SHA256()))

    def test_identity_signature_expiry_nonce_and_audience(self):
        def check(token):
            return verify_identity(token, self.jwks, issuer=AUTHORITY, audience='oaiapp_test', nonce='nonce', now=150)
        self.assertEqual(check(self.token())['sub'], 'test-user')
        for token in (self.token(exp=100), self.token(nonce='other'), self.token(aud='other'),
                      self.token(iss='https://evil.invalid'), self.token()[:-20] + 'x' * 20):
            with self.subTest(token=token[:12]), self.assertRaises(AuthError):
                check(token)

    def test_host_is_stable_and_callback_state_prevents_exchange(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = Mock()
            auth = AuthStore(tmp, client=client)
            one = auth.prepare('http://127.0.0.1:1234/auth/callback')
            two = auth.prepare('http://127.0.0.1:1235/auth/callback')
            self.assertEqual(one['host_id'], two['host_id'])
            self.assertNotEqual(one['state'], two['state'])
            with self.assertRaisesRegex(AuthError, 'state_mismatch'):
                auth.complete(one, {'state': ['wrong'], 'code': ['never-exchange']})
            client.post.assert_not_called()

    def test_refresh_rotation_and_protected_storage(self):
        requests = []
        def reply(request):
            requests.append(request)
            return httpx.Response(200, json={'token_type': 'Bearer', 'access_token': 'new-access',
                'refresh_token': 'new-refresh', 'expires_in': 3600})
        with tempfile.TemporaryDirectory() as tmp:
            auth = AuthStore(tmp, client=httpx.Client(transport=httpx.MockTransport(reply)))
            private_write(auth.record_path, {'issuer': AUTHORITY, 'subject': 'user', 'client_id': 'oaiapp_test',
                'access_token': 'old-access', 'refresh_token': 'old-refresh', 'expires_at': 1,
                'scopes': ['chatgpt.tokens.use.direct']})
            self.assertEqual(auth.access_token(), 'new-access')
            self.assertEqual(auth.access_token(), 'new-access')
            self.assertEqual(len(requests), 1)
            self.assertEqual(auth.read()['refresh_token'], 'new-refresh')
            public = json.dumps(auth.status())
            self.assertNotIn('new-access', public)
            self.assertNotIn('new-refresh', public)
            if os.name != 'nt':
                self.assertEqual(auth.record_path.stat().st_mode & 0o777, 0o600)

    def test_identity_only_does_not_send_inference_or_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = Mock()
            auth = AuthStore(tmp, client=client)
            private_write(auth.record_path, {'scopes': ['openid'], 'expires_at': 0})
            with self.assertRaisesRegex(AuthError, 'not_authorized'):
                auth.access_token()
            client.post.assert_not_called()


class ResponseTests(unittest.TestCase):
    def test_cancel_before_send_is_not_a_generation(self):
        def cancel():
            raise asyncio.CancelledError()
        store = Mock()
        provider = DirectProvider(model='test', reasoning_effort='low', auth_directory='/unused', store=store)
        result = provider.generate('text', 10, check_cancelled=cancel)
        self.assertEqual(result['status'], 'cancelled')
        self.assertFalse(result['generation_attempted'])
        store.access_token.assert_not_called()
        self.assertIsNone(result['usage']['input'])
        provider.close()

    def test_cancel_after_partial_text_preserves_unknown_usage(self):
        def cancel_on_text(text):
            raise asyncio.CancelledError()
        client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200,
            text='data: {"type":"response.output_text.delta","delta":"部分"}\n\n')))
        store = Mock()
        store.access_token.return_value = 'private-token'
        provider = DirectProvider(model='test', reasoning_effort='low', auth_directory='/unused', store=store, client=client)
        result = provider.generate('text', 10, on_text=cancel_on_text)
        self.assertEqual(result['status'], 'cancelled')
        self.assertEqual(result['translation'], '部分')
        self.assertFalse(result['completion_known'])
        self.assertIsNone(result['usage']['input'])
        provider.close()

    def test_silent_stream_is_closed_by_deadline(self):
        import threading
        class Silent(httpx.SyncByteStream):
            def __init__(self): self.closed = threading.Event()
            def __iter__(self):
                self.closed.wait(2)
                return
                yield b''
            def close(self): self.closed.set()
        stream = Silent()
        client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=stream)))
        store = Mock()
        store.access_token.return_value = 'private-token'
        provider = DirectProvider(model='test', reasoning_effort='low', auth_directory='/unused', store=store, client=client)
        result = provider.generate('text', .15)
        self.assertEqual(result['error']['type'], 'TimeoutError')
        self.assertLess(result['elapsed_seconds'], 1)
        self.assertIsNone(result['usage']['input'])
        provider.close()

    def test_cancel_shutdowns_socket_without_cross_thread_response_close(self):
        import threading
        class Silent(httpx.SyncByteStream):
            def __init__(self): self.wake = threading.Event()
            def __iter__(self):
                self.wake.wait(2)
                return
                yield b''
            def close(self): pass
        stream, network = Silent(), Mock()
        network.get_extra_info.return_value.shutdown.side_effect = lambda how: stream.wake.set()
        client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(
            200, stream=stream, extensions={'network_stream':network})))
        store = Mock(); store.access_token.return_value = 'local-test'
        provider = DirectProvider(model='test', reasoning_effort='low', auth_directory='/unused', store=store, client=client)
        result = provider.generate('text', .15)
        self.assertEqual(result['error']['type'], 'TimeoutError')
        self.assertLess(result['elapsed_seconds'], 1)
        network.get_extra_info.return_value.shutdown.assert_called_once()
        provider.close()

    def invoke(self, events, status=200):
        requests = []
        def respond(request):
            requests.append(json.loads(request.content))
            body = ''.join('data: ' + json.dumps(e) + '\n\n' for e in events)
            return httpx.Response(status, text=body)
        store = Mock()
        store.access_token.return_value = 'private-token'
        provider = DirectProvider(model='test-model', reasoning_effort='low', auth_directory='/unused',
                                  client=httpx.Client(transport=httpx.MockTransport(respond)), store=store)
        result = provider.generate('Translate a paper.', 10)
        return result, requests

    def test_completed_usage_deduplicates_and_never_logs_credentials(self):
        delta = {'type': 'response.output_text.delta', 'delta': '译文', 'sequence_number': 1}
        event = {'type': 'response.completed', 'response': {'status': 'completed',
            'usage': {'input_tokens': 50, 'output_tokens': 4, 'input_tokens_details': {'cached_tokens': 30}}}}
        result, requests = self.invoke([delta, delta, event, event])
        self.assertEqual((result['status'], result['translation']), ('ok', '译文'))
        self.assertEqual(result['usage']['input'], 50)
        self.assertEqual(len(requests), 1)
        self.assertEqual((requests[0]['store'], requests[0]['stream']), (False, True))
        self.assertNotIn('max_output_tokens', requests[0])
        self.assertNotIn('private-token', json.dumps(result))

    def test_failed_terminal_preserves_usage_and_partial_text(self):
        result, _ = self.invoke([{'type': 'response.output_text.delta', 'delta': '部分'},
             {'type': 'response.failed', 'response': {'usage': {'input_tokens': 40, 'output_tokens': 2},
                 'error': {'code': 'subscription_sharing_usage_limit_exceeded', 'message': 'secret'}}}])
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['usage']['input'], 40)
        self.assertEqual(result['translation'], '部分')
        self.assertNotIn('secret', json.dumps(result))

    def test_disconnected_stream_is_unknown_not_zero_and_not_retried(self):
        result, requests = self.invoke([{'type': 'response.output_text.delta', 'delta': '部分'}])
        self.assertEqual(result['status'], 'error')
        self.assertFalse(result['completion_known'])
        self.assertIsNone(result['usage']['input'])
        self.assertEqual(len(requests), 1)

    def test_auth_failure_is_not_counted_as_sent_generation(self):
        store = Mock()
        store.access_token.side_effect = AuthError('chatgpt_sign_in_required')
        provider = DirectProvider(model='test', reasoning_effort='low', auth_directory='/unused', store=store)
        result = provider.generate('text', 10)
        self.assertFalse(result['generation_attempted'])
        provider.close()


class ComparisonTests(unittest.TestCase):
    def prepare(self, directory):
        data, output = Path(directory) / 'data', Path(directory) / 'run'
        data.mkdir()
        samples = [{'id': f'{paper}-{i:02d}', 'source_text': 'The system preserves committed values.',
                    'origin': 'real', 'split': 'development', 'domain': paper}
                   for paper in direct_compare.BODY_IDS for i in (3, 7)]
        (data / 'samples.jsonl').write_text('\n'.join(json.dumps(x) for x in samples))
        direct_compare.prepare(data, output, model='test', effort='low', cli_path='/test/codex')
        return output

    def fake_provider(self):
        provider = Mock()
        provider.actual = {'model': 'test'}
        provider.client._ensure_started.return_value.call.return_value = {'account': {'email': 'same@example.test'}}
        provider.list_models.return_value = {'ok': True, 'models': [{'id': 'test'}]}
        provider.generate.return_value = {'status': 'ok', 'translation': '[{"id":0,"output":"系统保留已提交的值。"},{"id":1,"output":"系统保留已提交的值。"}]',
            'usage': {'input': 10, 'output': 6, 'cached_input': 0, 'reasoning': 0, 'availability': 'complete'},
            'error': None, 'elapsed_seconds': 1, 'ttft_seconds': .5}
        return provider

    def test_prepare_is_offline_fixed_and_guarded(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = self.prepare(tmp)
            manifest = read(output / 'experiment.json')
            self.assertEqual(len(manifest['schedule']), 54)
            self.assertEqual(len(manifest['cases']), 8)
            self.assertFalse(direct_compare.run(output)['executes_model'])
            self.assertFalse((output / 'calls.jsonl').exists())
            with patch.object(direct_compare, 'code_identity', return_value={'changed': True}), self.assertRaisesRegex(ValueError, 'Code changed'):
                direct_compare.run(output, execute=True)

    def test_paired_execution_and_account_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = self.prepare(tmp)
            store, direct, codex = Mock(), self.fake_provider(), self.fake_provider()
            store.status.return_value = {'plan_usage_authorized': True}
            store.read.return_value = {'email': 'same@example.test'}
            with patch.object(direct_compare, 'AuthStore', return_value=store), \
                 patch.object(direct_compare, 'DirectProvider', return_value=direct), \
                 patch.object(direct_compare, 'CodexProvider', return_value=codex), contextlib.redirect_stdout(io.StringIO()):
                result = direct_compare.run(output, execute=True)
            self.assertEqual(result['status'], 'completed')
            self.assertEqual((direct.generate.call_count, codex.generate.call_count), (27, 27))
            summary = read(output / 'summary.json')
            self.assertEqual(len(summary['paired_rounds']), 3)
            self.assertFalse(summary['baseline_promoted'])
            self.assertFalse(summary['performance_claim_allowed'])
            with self.assertRaisesRegex(ValueError, 'already has calls'):
                direct_compare.run(output, execute=True)

    def test_first_failure_stops_without_retry_and_retains_unknown_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = self.prepare(tmp)
            store, direct, codex = Mock(), self.fake_provider(), self.fake_provider()
            store.status.return_value = {'plan_usage_authorized': True}
            store.read.return_value = {'email': 'same@example.test'}
            direct.generate.return_value.update(status='error', translation='', error={'type': 'HTTPError', 'status_code': 403},
                usage={'input': None, 'output': None, 'cached_input': None, 'reasoning': None, 'availability': 'unavailable'})
            with patch.object(direct_compare, 'AuthStore', return_value=store), \
                 patch.object(direct_compare, 'DirectProvider', return_value=direct), \
                 patch.object(direct_compare, 'CodexProvider', return_value=codex), contextlib.redirect_stdout(io.StringIO()):
                result = direct_compare.run(output, execute=True)
            self.assertEqual(result['status'], 'incomplete')
            self.assertEqual(len(rows(output / 'calls.jsonl')), 1)
            codex.generate.assert_not_called()
            self.assertIsNone(rows(output / 'calls.jsonl')[0]['usage']['input'])


if __name__ == '__main__':
    unittest.main()
