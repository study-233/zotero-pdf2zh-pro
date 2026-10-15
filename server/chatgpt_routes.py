"""Loopback-only account management. OAuth secrets never enter plugin preferences."""
import threading
import time
from urllib.parse import urlsplit

from chatgpt_auth import AuthStore, USAGE_URL, login
from chatgpt_client import close_chatgpt_clients
from chatgpt_usage import UsageStore


class LoginSession:
    def __init__(self, directory):
        self.directory = directory
        self.lock = threading.Lock()
        self.thread = None
        self.cancelled = threading.Event()
        self.state = {'status': 'idle'}

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    def cancel(self):
        with self.lock:
            self.cancelled.set()
            if self.thread and self.thread.is_alive():
                self.state = {'status': 'cancelling'}
            return dict(self.state)

    def start(self, new_account=False):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return dict(self.state)
            self.cancelled = threading.Event()
            cancelled = self.cancelled
            self.state = {'status': 'starting', 'expiresAt': time.time() + 600}
            def ready(url):
                with self.lock:
                    if not cancelled.is_set():
                        self.state.update(status='pending', loginUrl=url)
            def work():
                try:
                    result = login(self.directory, ready=ready, cancelled=cancelled, new_account=new_account)
                    with self.lock:
                        self.state = {'status': 'completed' if result.get('authenticated') else
                                      'cancelled' if cancelled.is_set() else 'failed',
                                      'error': result.get('error')}
                    close_chatgpt_clients()
                except Exception:
                    with self.lock:
                        self.state = {'status': 'cancelled' if cancelled.is_set() else 'failed',
                                      'error': 'oauth_login_cancelled' if cancelled.is_set() else 'chatgpt_login_failed'}
            self.thread = threading.Thread(target=work, name='chatgpt-login', daemon=True)
            self.thread.start()
            return dict(self.state)


def install_chatgpt_routes(app, busy, directory=None):
    from flask import jsonify, request
    session = LoginSession(directory)
    app.extensions['chatgpt_login'] = session

    @app.before_request
    def protect_account_routes():
        if not request.path.startswith('/chatgpt/'):
            return None
        host = urlsplit('http://' + request.host).hostname
        if (request.remote_addr not in ('127.0.0.1', '::1') or host not in ('127.0.0.1', 'localhost', '::1')
                or request.headers.get('Origin') or request.headers.get('X-PDF2ZH-Client') != 'zotero'):
            return jsonify(status='error', code='chatgpt_local_only', message='ChatGPT 登录与用量管理仅支持本机 Zotero。'), 403

    @app.get('/chatgpt/status')
    def status():
        store = AuthStore(directory)
        try:
            state = store.status()
            return jsonify(status='ok', account=state, login=session.snapshot(), usageUrl=USAGE_URL)
        finally:
            store.client.close()

    @app.post('/chatgpt/login')
    def start():
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or type(data.get('newAccount', False)) is not bool:
            return jsonify(status='error', message='登录参数无效。'), 400
        if busy():
            return jsonify(status='error', message='请先结束正在执行的翻译，再登录或切换账号。'), 409
        return jsonify(status='ok', login=session.start(data.get('newAccount', False)))

    @app.post('/chatgpt/login/cancel')
    def cancel():
        return jsonify(status='ok', login=session.cancel())

    @app.post('/chatgpt/logout')
    def logout():
        if busy():
            return jsonify(status='error', message='请先结束正在执行的翻译，再退出账号。'), 409
        session.cancel()
        if session.thread and session.thread.is_alive():
            return jsonify(status='error', message='登录正在取消，请稍后重试退出。'), 409
        store = AuthStore(directory)
        try:
            result = store.logout()
            close_chatgpt_clients()
            return jsonify(status='ok', **result)
        finally:
            store.client.close()

    @app.get('/chatgpt/usage')
    def usage():
        store = AuthStore(directory)
        try:
            state = store.status()
            summary = UsageStore(store.directory).summary(state.get('account_fingerprint', ''))
            return jsonify(status='ok', usage=summary, usageUrl=USAGE_URL)
        finally:
            store.client.close()
