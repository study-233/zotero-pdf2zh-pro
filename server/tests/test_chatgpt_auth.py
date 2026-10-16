import json
import os
import socket
import sys
import traceback
from http.server import HTTPServer
from urllib.parse import urlsplit
import tempfile
import time
import threading
import unittest
from unittest.mock import Mock, patch
import httpx
from chatgpt_auth import AUTHORITY, AuthError, AuthStore, b64, login, private_write, verify_identity

class LoginCancellationTests(unittest.TestCase):
    def test_browser_idle_preconnection_cannot_block_login_cancellation(self):
        accepted, ready, cancelled = threading.Event(), threading.Event(), threading.Event()
        urls, results, errors = [], [], []
        class ObservedServer(HTTPServer):
            def get_request(self):
                connection = super().get_request()
                accepted.set()
                return connection
        def on_ready(url):
            urls.append(url)
            ready.set()
        with tempfile.TemporaryDirectory() as directory, patch('chatgpt_auth.HTTPServer', ObservedServer):
            def run():
                try:
                    results.append(login(directory, ready=on_ready, cancelled=cancelled, max_seconds=5))
                except Exception as error:
                    errors.append(error)
                    ready.set()
            thread = threading.Thread(target=run, daemon=True)
            thread.start()
            connection = None
            try:
                if not ready.wait(3):
                    frame = sys._current_frames().get(thread.ident)
                    self.fail("login startup timed out\n" + "".join(traceback.format_stack(frame)))
                self.assertFalse(errors)
                url = urlsplit(urls[0])
                connection = socket.create_connection(('127.0.0.1', url.port), timeout=2)
                # A browser preconnects but does not send an HTTP request line.
                self.assertTrue(accepted.wait(2))
                cancelled.set()
                thread.join(1.5)
                self.assertFalse(thread.is_alive(), 'idle browser connection blocked cancel')
                self.assertEqual(results[0]['error'], 'oauth_login_cancelled')
                self.assertFalse(errors)
            finally:
                cancelled.set()
                if connection:
                    connection.close()
                thread.join(3)

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

    def test_full_callback_verifies_identity_before_registration_and_late_cancel_does_not_replace_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            cancelled = threading.Event()
            stamp = time.time()
            token = self.token(iat=stamp, exp=stamp+3600)
            def reply(request):
                if request.method == 'POST':
                    return httpx.Response(200, json={'token_type':'Bearer','access_token':'owned',
                        'refresh_token':'owned-refresh','id_token':token,'expires_in':3600,
                        'scope':'openid chatgpt.tokens.use.direct'})
                if request.url.path.endswith('openid-configuration'):
                    return httpx.Response(200, json={'issuer':AUTHORITY,'jwks_uri':AUTHORITY+'/test-keys'})
                return httpx.Response(200,json=self.jwks)
            store = AuthStore(tmp, client=httpx.Client(transport=httpx.MockTransport(reply)))
            self.addCleanup(store.client.close)
            attempt = {'client_id':'dynamic_agent_client','state':'state','nonce':'nonce','old':None,
                       'host_id':'host','verifier':'verifier','callback':'http://127.0.0.1:1234/auth/callback'}
            query = {'client_id':['oaiapp_test'],'code':['code'],'state':['state']}
            cancelled.set()
            with self.assertRaisesRegex(AuthError,'oauth_login_cancelled'): store.complete(attempt,query,cancelled)
            self.assertFalse(store.record_path.exists())
            cancelled.clear()
            self.assertTrue(store.complete(attempt,query,cancelled)['plan_usage_authorized'])
            self.assertEqual(store.read()['client_id'], 'oaiapp_test')
            with self.assertRaisesRegex(AuthError,'validation_failed'):
                store.complete({**attempt,'nonce':'bad'},query)
            self.assertEqual(store.read()['access_token'],'owned')

    def test_default_directory_follows_runtime_data_dir_after_module_import(self):
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'PDF2ZH_DATA_DIR': tmp}):
            with patch.dict(os.environ):
                os.environ.pop('PDF2ZH_CHATGPT_AUTH_DIR', None)
                store = AuthStore()
                self.addCleanup(store.client.close)
                self.assertEqual(store.directory, (Path(tmp)/'chatgpt-auth').resolve())
