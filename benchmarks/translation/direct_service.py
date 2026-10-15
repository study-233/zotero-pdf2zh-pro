"""Owned benchmark service: inject SIWC at the provider boundary, including spawn workers.

The normal server never imports this module. Product PDF extraction, prompts,
validation, recovery, cache and typesetting stay on the same implementation.
"""
from __future__ import annotations

import asyncio
import atexit
import os
from pathlib import Path
import sys
import threading
import uuid

from .common import ROOT, append, digest, now
from .chatgpt_auth import DEFAULT_AUTH
from .chatgpt_direct import DirectProvider

sys.path.insert(0, str(ROOT / 'server'))
import codex_client


class DirectClient:
    protocol = 'responses'
    accepts_call_id = True

    def __init__(self, auth_directory, ledger_directory):
        self.auth_directory, self.ledger_directory = auth_directory, Path(ledger_directory)
        self.lock, self.ready = threading.Lock(), set()

    def session(self):
        return self

    def provider(self, model, effort):
        return DirectProvider(model=model, reasoning_effort=effort, auth_directory=self.auth_directory,
                              instructions=codex_client.INSTRUCTIONS)

    def check_ready(self, model, reasoning_effort=None, *, check_cancelled=None):
        if check_cancelled:
            check_cancelled()
        if reasoning_effort != 'low':
            raise codex_client.CodexError('codex_invalid_reasoning', '本次实验固定使用 low。')
        with self.lock:
            if model not in self.ready:
                provider = self.provider(model, reasoning_effort)
                try:
                    result = provider.list_models()
                    if not result.get('ok') or model not in {m['id'] for m in result['models']}:
                        raise codex_client.CodexError('codex_model_unavailable', '独立授权未提供所选模型。')
                    self.ready.add(model)
                finally:
                    provider.close()
        if check_cancelled:
            check_cancelled()
        return {'model': model, 'reasoningEffort': reasoning_effort, 'serviceTier': 'default',
                'authenticated': True, 'transport': 'chatgpt_direct_responses_sse', 'version': 'benchmark-siwc-1'}

    def translate(self, prompt, *, model, reasoning_effort, timeout, check_cancelled=None,
                  check_admission=None, on_text=None, on_usage=None, call_id=None):
        self.check_ready(model, reasoning_effort, check_cancelled=check_cancelled)
        if check_admission:
            check_admission()
        call_id = call_id or uuid.uuid4().hex
        path = self.ledger_directory / f'{os.getpid()}.jsonl'
        with self.lock:
            append(path, {'event': 'started', 'call_id': call_id, 'at': now(), 'prompt_sha256': digest(prompt.encode())})
        provider = self.provider(model, reasoning_effort)
        try:
            result = provider.generate(prompt, timeout, on_text=on_text, check_cancelled=check_cancelled)
        finally:
            provider.close()
        with self.lock:
            append(path, {'event': 'finished', 'call_id': call_id, 'at': now(), 'prompt': prompt, **result})
        usage = {target: result['usage'][source] for source, target in
                 (('input', 'prompt_tokens'), ('output', 'completion_tokens'),
                  ('cached_input', 'cache_hit_tokens'), ('reasoning', 'reasoning_tokens'))
                 if result['usage'][source] is not None}
        if on_usage:
            on_usage(usage)
        if result['status'] == 'cancelled':
            # Preserve the caller's cancellation contract: selection uses a
            # TextTranslationError, while PDF workers use CancelledError.
            if check_cancelled:
                check_cancelled()
            raise asyncio.CancelledError()
        if result['status'] != 'ok':
            code = (result.get('error') or {}).get('code', '')
            kind = (result.get('error') or {}).get('type', '')
            if kind == 'AuthError':
                mapped, status = 'codex_not_logged_in', 401
            elif 'usage_limit' in code:
                mapped, status = 'codex_quota_exhausted', 429
            else:
                mapped, status = ('codex_timeout', 504) if kind in {'TimeoutError', 'ReadTimeout'} else ('codex_protocol_error', 502)
            raise codex_client.CodexError(mapped, '直接通道调用未完成；详情见脱敏实验流水。', status)
        return codex_client.CodexResult(result['translation'], usage)

    def close(self):
        pass  # Each request owns and closes its HTTP connections.


_clients, _lock = {}, threading.Lock()


def get_client(*args, **kwargs):
    with _lock:
        if os.getpid() not in _clients:
            _clients[os.getpid()] = DirectClient(os.environ['PDF2ZH_EXPERIMENT_DIRECT_AUTH'],
                                                os.environ['PDF2ZH_EXPERIMENT_DIRECT_LEDGER'])
        return _clients[os.getpid()]


def close_clients():
    for client in _clients.values():
        client.close()


def install():
    if not all(os.environ.get(k) for k in ('PDF2ZH_EXPERIMENT_DIRECT_AUTH', 'PDF2ZH_EXPERIMENT_DIRECT_LEDGER',
                                         'PDF2ZH_BENCHMARK_LEDGER_DIR', 'PDF2ZH_DATA_DIR')):
        raise RuntimeError('Direct service requires an isolated benchmark workspace')
    codex_client.get_codex_client = get_client
    codex_client.close_codex_clients = close_clients
    atexit.register(close_clients)


# Multiprocessing spawn re-executes this module as __mp_main__; install in both.
if __name__ in {'__main__', '__mp_main__'}:
    install()
if __name__ == '__main__':
    import server
    server.main()
