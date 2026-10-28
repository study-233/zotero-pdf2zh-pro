"""Owned benchmark service: frozen client source and content-free HTTP timings."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import threading
import time

import httpx


def install():
    required = ('PDF2ZH_BENCHMARK_CLIENT_FILE', 'PDF2ZH_BENCHMARK_CLIENT_SHA256',
                'PDF2ZH_BENCHMARK_CONNECTION_TRACE', 'PDF2ZH_BENCHMARK_LEDGER_DIR', 'PDF2ZH_DATA_DIR')
    if not all(os.environ.get(key) for key in required):
        raise RuntimeError('Connection probe requires an isolated benchmark workspace')
    path = Path(os.environ[required[0]])
    if hashlib.sha256(path.read_bytes()).hexdigest() != os.environ[required[1]]:
        raise RuntimeError('Frozen benchmark client changed')
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'server'))
    spec = importlib.util.spec_from_file_location('chatgpt_client', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules['chatgpt_client'] = module
    spec.loader.exec_module(module)
    if os.environ.get('PDF2ZH_BENCHMARK_WORKER_CAP'):
        if os.environ['PDF2ZH_BENCHMARK_WORKER_CAP'] != '4':
            raise RuntimeError('Unexpected experimental worker cap')
        from pdf2zh_pro.translator.chatgpt import ChatGPTTranslator
        # In this owned service only. PDF tasks supply a shared semaphore sized
        # to the effective pool, so the normal standalone fallback stays intact.
        ChatGPTTranslator.recommended_pool_max_workers = 4
    root = Path(os.environ[required[2]])
    root.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()

    def save(record):
        with lock, (root / f'{os.getpid()}.jsonl').open('a') as output:
            output.write(json.dumps(record) + '\n')

    original = httpx.AsyncClient
    class TracedClient(original):
        async def send(self, request, *args, **kwargs):
            if request.url.path not in ('/v1/responses', '/v1/models'):
                return await super().send(request, *args, **kwargs)
            started, active = time.monotonic(), {}
            record = {'started_at': time.time(), 'method': request.method, 'path': request.url.path,
                      'body_sha256': hashlib.sha256(request.content).hexdigest(), 'stages': []}
            async def trace(name, info):
                if name.endswith('.started'):
                    active[name[:-8]] = time.monotonic()
                elif name.endswith(('.complete', '.failed')):
                    key = name.rsplit('.', 1)[0]
                    if key in active:
                        record['stages'].append({'name': key, 'seconds': time.monotonic() - active.pop(key),
                                                 'failed': name.endswith('.failed')})
            request.extensions['trace'] = trace
            try:
                response = await super().send(request, *args, **kwargs)
            except BaseException as error:
                record.update(error_type=type(error).__name__, elapsed_seconds=time.monotonic() - started)
                save(record)
                raise
            record.update(status=response.status_code, headers_seconds=time.monotonic() - started)
            record['response_headers'] = {key: response.headers[key] for key in
                ('retry-after', 'x-request-id', 'x-ratelimit-remaining-requests',
                 'x-ratelimit-remaining-tokens', 'x-ratelimit-reset-requests', 'x-ratelimit-reset-tokens')
                if key in response.headers}
            if response.status_code >= 400:
                await response.aread()
                try:
                    body = response.json()
                    error = body.get('error', body.get('detail', {}))
                    if isinstance(error, dict):
                        record['error_code'] = error.get('code') or error.get('type')
                except (ValueError, AttributeError):
                    pass
            if not kwargs.get('stream'):
                record['elapsed_seconds'] = time.monotonic() - started
                save(record)
                return response
            underlying = response.stream
            class Stream(httpx.AsyncByteStream):
                closed = False
                async def __aiter__(self):
                    async for chunk in underlying:
                        yield chunk
                async def aclose(self):
                    try:
                        await underlying.aclose()
                    finally:
                        if not self.closed:
                            self.closed = True
                            record['elapsed_seconds'] = time.monotonic() - started
                            save(record)
            response.stream = Stream()
            return response
    module.httpx.AsyncClient = TracedClient


if __name__ in {'__main__', '__mp_main__'}:
    install()
if __name__ == '__main__':
    import server
    server.main()
