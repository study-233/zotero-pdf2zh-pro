"""Official ChatGPT plan OAuth for the local service. Never reads Codex credentials.

OAuth state/nonce/verifier live in memory; only this app's own credential record
is persisted, with owner-only permissions. No model requests are made here.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
from socketserver import TCPServer
import tempfile
import time
from urllib.parse import parse_qs, urlencode, urlsplit
import uuid
from runtime_storage import default_data_directory


AUTHORITY = 'https://auth.openai.com'
RESOURCE = 'https://api.openai.com/v1'
AUTHORIZE = AUTHORITY + '/api/accounts/authorize'
TOKEN = AUTHORITY + '/api/accounts/oauth/token'
SCOPE = 'openid profile email offline_access resource.invoke chatgpt.tokens.use.direct'
def auth_directory(directory=None):
    return Path(directory if directory is not None else os.getenv(
        'PDF2ZH_CHATGPT_AUTH_DIR', str(Path(os.getenv('PDF2ZH_DATA_DIR',
        str(default_data_directory(__file__)))) / 'chatgpt-auth'))).expanduser().resolve()


DEFAULT_AUTH = auth_directory()
USAGE_URL = 'https://chatgpt.com/settings/usage'
SIGN_IN_REQUIRED_MESSAGE = 'ChatGPT 登录已失效，请重新登录。'


class AuthError(ValueError):
    """Only a fixed public error code, never remote text or credentials."""


def b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode()


def unb64(value):
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))


def private_write(path, data):
    path = Path(path)
    if path.is_symlink() or path.parent.is_symlink():
        raise AuthError('credential_symlink_rejected')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.credential-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            json.dump(data, out, ensure_ascii=False)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def credential_lock(directory):
    directory = Path(directory)
    if directory.is_symlink():
        raise AuthError('credential_symlink_rejected')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    path = directory / '.lock'
    if path.is_symlink():
        raise AuthError('credential_symlink_rejected')
    with path.open('a+b') as handle:
        os.chmod(path, 0o600)
        if os.name == 'nt':
            import msvcrt
            # Windows can lock beyond EOF. Writing first races with another
            # handle that has already locked the initially empty file.
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == 'nt':
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def verify_identity(token, jwks, *, issuer, audience, nonce, now=None):
    """Verify RS256 signature before trusting claims; fail closed on other algs."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    try:
        head, body, signature = token.split('.')
        header = json.loads(unb64(head))
        if header.get('alg') != 'RS256':
            raise ValueError()
        keys = [k for k in jwks['keys'] if k.get('kid') == header.get('kid')
                and k.get('kty') == 'RSA' and k.get('use', 'sig') == 'sig'
                and k.get('alg', 'RS256') == 'RS256']
        if len(keys) != 1:
            raise ValueError()
        key = keys[0]
        rsa.RSAPublicNumbers(int.from_bytes(unb64(key['e']), 'big'),
                             int.from_bytes(unb64(key['n']), 'big')).public_key().verify(
            unb64(signature), (head + '.' + body).encode(), padding.PKCS1v15(), hashes.SHA256())
        claims = json.loads(unb64(body))
        clock = time.time() if now is None else now
        audiences = claims.get('aud')
        audiences = audiences if isinstance(audiences, list) else [audiences]
        if (claims.get('iss') != issuer or audience not in audiences
                or (len(audiences) > 1 and claims.get('azp') != audience)
                or ('azp' in claims and claims['azp'] != audience)
                or type(claims.get('exp')) not in (int, float) or claims['exp'] <= clock
                or type(claims.get('iat')) not in (int, float) or claims['iat'] > clock + 30
                or claims.get('nbf', 0) > clock + 30
                or not isinstance(claims.get('sub'), str) or not claims['sub']
                or not isinstance(claims.get('nonce'), str)
                or not secrets.compare_digest(claims['nonce'], nonce)):
            raise ValueError()
        return claims
    except Exception:
        raise AuthError('id_token_validation_failed') from None


class AuthStore:
    def __init__(self, directory=None, client=None):
        import httpx
        self.directory = auth_directory(directory)
        self.client = client or httpx.Client(timeout=25, follow_redirects=False)
        self.record_path = self.directory / 'credentials.json'

    def read(self):
        if self.record_path.is_symlink():
            raise AuthError('credential_symlink_rejected')
        if not self.record_path.exists():
            raise AuthError('chatgpt_sign_in_required')
        if os.name != 'nt' and self.record_path.stat().st_mode & 0o077:
            raise AuthError('credential_permissions_invalid')
        return json.loads(self.record_path.read_text())

    def status(self):
        try:
            record = self.read()
        except (AuthError, OSError, ValueError):
            return {'authenticated': False, 'error': 'chatgpt_sign_in_required'}
        identity = record['issuer'] + '\0' + record['subject'] + '\0' + record['client_id']
        requires_login = bool(record.get('requires_login') or
                              (record['expires_at'] <= time.time() and not record.get('refresh_token')))
        return {'authenticated': not requires_login, 'requires_login': requires_login,
                'plan_usage_authorized': 'chatgpt.tokens.use.direct' in record['scopes'],
                'account_fingerprint': hashlib.sha256(identity.encode()).hexdigest(),
                'account_label': record.get('email') or 'ChatGPT',
                'expires_in_seconds': max(0, int(record['expires_at'] - time.time())),
                'refresh_available': bool(record.get('refresh_token')), 'inference_verified': False}

    def mark_sign_in_required(self, rejected_token):
        # A late rejection from an old request must not invalidate a new login
        # or a token already rotated by another worker.
        with credential_lock(self.directory):
            if not self.record_path.exists():
                return
            record = self.read()
            if record.get('access_token') == rejected_token:
                record['requires_login'] = True
                private_write(self.record_path, record)

    def _post_token(self, fields):
        response = self.client.post(TOKEN, data=fields)
        if response.status_code != 200:
            # Only whitelist stable error codes; never propagate raw descriptions.
            try:
                code = response.json().get('error')
            except Exception:
                code = None
            if isinstance(code, str) and code in {'invalid_grant', 'invalid_client', 'invalid_refresh_token', 'refresh_token_reused'}:
                raise AuthError('oauth_' + code)
            response.raise_for_status()
        data = response.json()
        if (not isinstance(data.get('access_token'), str) or not data['access_token']
                or type(data.get('expires_in')) not in (int, float) or data['expires_in'] <= 0
                or str(data.get('token_type', '')).lower() != 'bearer'):
            raise AuthError('invalid_token_response')
        return data

    def logout(self):
        """Revoke only this application's session. Preserve its registration."""
        confirmed = False
        with credential_lock(self.directory):
            if not self.record_path.exists():
                return {'authenticated': False, 'revocationConfirmed': True}
            record = self.read()
            try:
                metadata = self.client.get(AUTHORITY + '/.well-known/openid-configuration').json()
                endpoint = urlsplit(metadata.get('revocation_endpoint', ''))
                if (endpoint.scheme != 'https' or endpoint.hostname != 'auth.openai.com'
                        or endpoint.username or endpoint.password):
                    raise AuthError('invalid_revocation_endpoint')
                response = self.client.post(endpoint.geturl(), data={
                    'token': record.get('refresh_token', ''), 'token_type_hint': 'refresh_token',
                    'client_id': record['client_id']})
                confirmed = response.status_code == 200
            except Exception:
                pass  # Local sign-out still completes; report unconfirmed revocation.
            self.record_path.unlink()
        return {'authenticated': False, 'revocationConfirmed': confirmed}

    def access_token(self, *, force_refresh=False, rejected_token=None, expected_account=None):
        with credential_lock(self.directory):
            record = self.read()
            if expected_account:
                identity = record['issuer'] + '\0' + record['subject'] + '\0' + record['client_id']
                if hashlib.sha256(identity.encode()).hexdigest() != expected_account:
                    raise AuthError('chatgpt_account_changed')
            if record.get('requires_login'):
                raise AuthError('chatgpt_sign_in_required')
            if 'chatgpt.tokens.use.direct' not in record['scopes']:
                raise AuthError('chatgpt_plan_usage_not_authorized')
            # A concurrent request may already have rotated the rejected token.
            # Compare under the same lock that protects refresh-token rotation.
            if (force_refresh or (rejected_token is not None and record['access_token'] == rejected_token)
                    or record['expires_at'] <= time.time() + 120):
                if not record.get('refresh_token'):
                    record['requires_login'] = True
                    private_write(self.record_path, record)
                    raise AuthError('chatgpt_sign_in_required')
                try:
                    data = self._post_token({'grant_type': 'refresh_token', 'client_id': record['client_id'],
                                             'refresh_token': record['refresh_token'], 'resource': RESOURCE})
                    if not data.get('refresh_token'):
                        raise AuthError('refresh_replacement_missing')
                except AuthError as error:
                    if str(error) != 'invalid_token_response':
                        record['requires_login'] = True
                        private_write(self.record_path, record)
                    raise
                record.update(access_token=data['access_token'], refresh_token=data['refresh_token'],
                              expires_at=time.time() + data['expires_in'],
                              scopes=data.get('scope', ' '.join(record['scopes'])).split())
                private_write(self.record_path, record)
                if 'chatgpt.tokens.use.direct' not in record['scopes']:
                    record['requires_login'] = True
                    private_write(self.record_path, record)
                    raise AuthError('chatgpt_plan_usage_not_authorized')
            return record['access_token']

    def prepare(self, callback, *, new_account=False):
        with credential_lock(self.directory):
            host_path = self.directory / 'host.json'
            if host_path.is_symlink():
                raise AuthError('credential_symlink_rejected')
            host = json.loads(host_path.read_text()) if host_path.exists() else {'id': 'urn:uuid:' + str(uuid.uuid4())}
            private_write(host_path, host)
            registration_path = self.directory / 'registration.json'
            if registration_path.is_symlink():
                raise AuthError('credential_symlink_rejected')
            registration = json.loads(registration_path.read_text()) if registration_path.exists() else {}
            old = self.read() if self.record_path.exists() else None
            if new_account:
                old, registration = None, {}
        client_id = old['client_id'] if old else registration.get('client_id', 'dynamic_agent_client')
        state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        query = dict(client_id=client_id, ext_agent_host_id=host['id'], response_type='code',
                     redirect_uri=callback, scope=SCOPE, resource=RESOURCE, state=state, nonce=nonce,
                     code_challenge_method='S256', code_challenge=b64(hashlib.sha256(verifier.encode()).digest()))
        if client_id == 'dynamic_agent_client':
            query['agent_name_hint'] = 'Zotero PDF2zh Pro'
        # No ID token in the URL: returning users choose their account explicitly.
        return {'state': state, 'nonce': nonce, 'verifier': verifier, 'callback': callback,
                'host_id': host['id'], 'client_id': client_id, 'old': old,
                'url': AUTHORIZE + '?' + urlencode(query)}

    def complete(self, attempt, query, cancelled=None):
        if any(len(values) != 1 for values in query.values()):
            raise AuthError('ambiguous_oauth_callback')
        returned = {k: v[0] for k, v in query.items()}
        if not secrets.compare_digest(returned.get('state', ''), attempt['state']):
            raise AuthError('oauth_state_mismatch')
        if 'error' in returned:
            raise AuthError('oauth_consent_not_completed')
        issued = returned.get('client_id', attempt['client_id'])
        if (not isinstance(issued, str) or not issued or issued == 'dynamic_agent_client'
                or len(issued) > 512 or not returned.get('code')):
            raise AuthError('oauth_registration_incomplete')
        if attempt['client_id'] != 'dynamic_agent_client' and issued != attempt['client_id']:
            raise AuthError('oauth_client_mismatch')
        with credential_lock(self.directory):
            data = self._post_token({'grant_type': 'authorization_code', 'client_id': issued,
                                    'code': returned['code'], 'code_verifier': attempt['verifier'],
                                    'redirect_uri': attempt['callback'], 'resource': RESOURCE})
            metadata_response = self.client.get(AUTHORITY + '/.well-known/openid-configuration')
            metadata_response.raise_for_status()
            metadata = metadata_response.json()
            jwks_url = metadata.get('jwks_uri', '')
            parsed = urlsplit(jwks_url)
            if (metadata.get('issuer') != AUTHORITY or parsed.scheme != 'https'
                    or parsed.hostname != 'auth.openai.com' or parsed.username or parsed.password):
                raise AuthError('invalid_oidc_metadata')
            jwks_response = self.client.get(jwks_url)
            jwks_response.raise_for_status()
            claims = verify_identity(data.get('id_token', ''), jwks_response.json(),
                                     issuer=AUTHORITY, audience=issued, nonce=attempt['nonce'])
            if attempt['old'] and claims['sub'] != attempt['old']['subject']:
                raise AuthError('oauth_account_mismatch')
            record = dict(client_id=issued, ext_agent_host_id=attempt['host_id'], issuer=claims['iss'],
                          subject=claims['sub'], email=claims.get('email'), access_token=data['access_token'],
                          refresh_token=data.get('refresh_token'), id_token=data['id_token'],
                          scopes=data.get('scope', '').split(), expires_at=time.time() + data['expires_in'])
            if cancelled and cancelled.is_set():
                raise AuthError('oauth_login_cancelled')
            private_write(self.record_path, record)
            private_write(self.directory / 'registration.json', {'client_id': issued})
        return self.status()


def login(directory=None, *, max_seconds=600, ready=None, cancelled=None, new_account=False):
    store = AuthStore(directory)
    outcome = {}
    attempt = None
    route = '/start/' + secrets.token_urlsafe(24)

    class CallbackServer(HTTPServer):
        def server_bind(self):
            # The callback uses a numeric loopback address; reverse DNS can block
            # startup on macOS and is unnecessary for OAuth or Host validation.
            TCPServer.server_bind(self)
            self.server_name, self.server_port = self.server_address[:2]

    class Handler(BaseHTTPRequestHandler):
        # HTTPServer.timeout only bounds accept(), not reads on an accepted
        # socket. Browsers may preconnect without sending an HTTP request.
        timeout = .5

        def log_message(self, *args):
            pass  # callback query contains an authorization code

        def respond(self, code, body='', location=None):
            self.send_response(code)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Security-Policy', "default-src 'none'; frame-ancestors 'none'")
            if location:
                self.send_header('Location', location)
            self.end_headers()
            self.wfile.write(body.encode())

        def do_GET(self):
            if cancelled and cancelled.is_set():
                self.respond(410, 'Authorization cancelled.'); return
            if self.headers.get('Host') != f'127.0.0.1:{server.server_port}':
                self.respond(400); return
            parsed = urlsplit(self.path)
            if parsed.path == route:
                self.respond(302, location=attempt['url'])
            elif parsed.path == '/auth/callback':
                if outcome:
                    self.respond(409, 'Authorization attempt already completed.'); return
                try:
                    outcome.update(store.complete(attempt, parse_qs(parsed.query, keep_blank_values=True), cancelled))
                except AuthError as e:
                    if str(e) in {'oauth_state_mismatch', 'ambiguous_oauth_callback'}:
                        self.respond(400, 'Invalid authorization callback.'); return
                    outcome.update(authenticated=False, error=str(e))
                except Exception as e:
                    outcome.update(authenticated=False, error='oauth_callback_' + type(e).__name__)
                self.respond(303, location='/done')
            elif parsed.path == '/done':
                self.respond(200, '<meta charset="utf-8"><title>PDF2zh Pro</title><h2>' +
                             ('授权已完成，可以返回 Zotero。' if outcome.get('authenticated') else
                              '授权未完成，请返回 Zotero 查看状态。') + '</h2>')
            else:
                self.respond(404)

    server = CallbackServer(('127.0.0.1', 0), Handler)
    server.timeout = .5
    try:
        attempt = store.prepare(f'http://127.0.0.1:{server.server_port}/auth/callback', new_account=new_account)
        url = f'http://127.0.0.1:{server.server_port}{route}'
        if ready:
            ready(url)
        deadline = time.monotonic() + max_seconds
        while not outcome and time.monotonic() < deadline and not (cancelled and cancelled.is_set()):
            server.handle_request()
        if outcome:
            # Permit the redirect to /done to finish; never wait indefinitely.
            server.handle_request()
        return outcome or {'authenticated': False, 'error': 'oauth_login_cancelled' if cancelled and cancelled.is_set() else 'oauth_login_timeout'}
    finally:
        server.server_close()
        store.client.close()
