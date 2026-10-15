"""Explicit SIWC Responses adapter used only by the P0 benchmark."""
from __future__ import annotations

import asyncio
import json
import re
import socket
import threading
import time

from .chatgpt_auth import AuthStore, RESOURCE
from .common import digest
from .providers import usage


def error_code(value):
    # Raw error messages may echo user input or credentials.
    candidate = value.get('code') if isinstance(value, dict) else None
    return candidate if isinstance(candidate, str) and re.fullmatch(r'[a-zA-Z0-9_.-]{1,100}', candidate) else None


def sse_events(lines):
    data = []
    for line in lines:
        if not line:
            if data:
                value = '\n'.join(data)
                if value != '[DONE]':
                    yield json.loads(value)
                data = []
        elif line.startswith('data:'):
            data.append(line[5:].lstrip(' '))
    if data:
        value = '\n'.join(data)
        if value != '[DONE]':
            yield json.loads(value)


def completed_text(response):
    return ''.join(c.get('text', '') for item in response.get('output', [])
                   if item.get('type') == 'message' for c in item.get('content', [])
                   if c.get('type') == 'output_text')


class DirectProvider:
    def __init__(self, *, model, reasoning_effort, auth_directory, client=None, store=None, instructions=''):
        import httpx
        self.model, self.reasoning_effort = model, reasoning_effort
        self.store = store or AuthStore(auth_directory)
        self.client = client or httpx.Client(timeout=120, follow_redirects=False)
        self.instructions = instructions

    def list_models(self):
        response = self.client.get(RESOURCE + '/models',
                                   headers={'Authorization': 'Bearer ' + self.store.access_token()}, timeout=30)
        if response.status_code != 200:
            return {'ok': False, 'status_code': response.status_code, 'models': []}
        data = response.json()
        models = data.get('models')
        if not isinstance(models, list):
            return {'ok': False, 'error': 'unexpected_model_catalog', 'models': []}
        return {'ok': True, 'models': [{'id': m.get('slug'), 'display_name': m.get('display_name')}
                                      for m in models if m.get('visibility') == 'list']}

    def generate(self, prompt, timeout, *, on_text=None, check_cancelled=None):
        started = time.monotonic()
        first_event = first_text = headers_at = request_started = None
        used, output, event_counts, terminal, error = {}, [], {}, None, None
        actual, submitted, status_code = {}, False, None
        body = {'model': self.model, 'input': [{'role': 'user', 'content': prompt}],
                'store': False, 'stream': True, 'service_tier': 'default',
                'reasoning': {'effort': self.reasoning_effort}}
        if self.instructions:
            body['instructions'] = self.instructions
        seen = set()
        request_id = response_id = None
        stopped, stopped_errors, active_response = threading.Event(), [], []
        def check():
            if check_cancelled:
                check_cancelled()
            if time.monotonic() - started >= timeout:
                raise TimeoutError()
        def watch():
            # Poll cancellation even while the upstream sends no events.
            while not stopped.wait(.1):
                try:
                    check()
                except (Exception, asyncio.CancelledError) as exc:
                    stopped_errors.append(exc)
                    if active_response:
                        try:
                            # Closing an httpx sync response from another thread
                            # can wait on the reader's TLS lock. Shutdown its
                            # socket first; the reader then owns normal cleanup.
                            network = active_response[0].extensions.get('network_stream')
                            connection = network.get_extra_info('socket') if network else None
                            if connection is not None:
                                connection.shutdown(socket.SHUT_RDWR)
                            else:
                                active_response[0].close()
                        except Exception:
                            pass
                    return
        watcher = threading.Thread(target=watch, daemon=True, name='siwc-cancel-watch')
        try:
            check()
            secret = self.store.access_token()
            check()
            request_started = time.monotonic() - started
            submitted = True  # a network failure after this point may be ambiguous
            watcher.start()
            with self.client.stream('POST', RESOURCE + '/responses', json=body,
                                    headers={'Authorization': 'Bearer ' + secret}, timeout=timeout) as response:
                active_response.append(response)
                check()
                status_code = response.status_code
                request_id = response.headers.get('x-request-id')
                headers_at = time.monotonic() - started
                if status_code != 200:
                    try:
                        raw_error = json.loads(response.read())
                    except Exception:
                        raw_error = {}
                    error = {'type': 'HTTPError', 'status_code': status_code,
                             'code': error_code(raw_error.get('error'))}
                    terminal = 'http_error'
                else:
                    for event in sse_events(response.iter_lines()):
                        check()
                        if first_event is None:
                            first_event = time.monotonic() - started
                        kind = event.get('type', '')
                        sequence = event.get('sequence_number')
                        if type(sequence) is int:
                            if sequence in seen:
                                continue
                            seen.add(sequence)
                        event_counts[kind] = event_counts.get(kind, 0) + 1
                        snapshot = event.get('response') or {}
                        if snapshot.get('id'):
                            response_id = snapshot['id']
                        if isinstance(snapshot.get('usage'), dict):
                            used = snapshot['usage']
                        for key in ('model', 'service_tier', 'reasoning'):
                            if key in snapshot:
                                actual[key] = snapshot[key]
                        if kind == 'response.output_text.delta' and isinstance(event.get('delta'), str):
                            if first_text is None:
                                first_text = time.monotonic() - started
                            output.append(event['delta'])
                            if on_text:
                                on_text(event['delta'])
                        if kind == 'response.completed':
                            terminal = snapshot.get('status')
                            final = completed_text(snapshot)
                            if final:
                                output = [final]
                            if terminal != 'completed':
                                error = {'type': 'InvalidTerminalStatus'}
                            break
                        if kind in {'response.failed', 'response.incomplete', 'error'}:
                            terminal = kind
                            error = {'type': 'ResponseError', 'code': error_code(snapshot.get('error') or event.get('error') or event)}
                            break
            if stopped_errors:
                raise stopped_errors[0]
            if error is None and (terminal != 'completed' or not ''.join(output).strip()):
                error = {'type': 'IncompleteOutput'}
        except (Exception, KeyboardInterrupt, asyncio.CancelledError) as exc:
            exc = stopped_errors[0] if stopped_errors else exc
            error = {'type': type(exc).__name__}
            if type(exc).__name__ == 'AuthError':
                error['code'] = str(exc)
            elif isinstance(getattr(exc, 'code', None), str):
                error['code'] = error_code({'code': exc.code})
        finally:
            stopped.set()
            if watcher.ident is not None:
                watcher.join(timeout=.2)
        normalized = usage(used)
        cancelled = error and (error['type'] in {'KeyboardInterrupt', 'CancelledError'}
                               or error.get('code') in {'codex_cancelled', 'selection_cancelled'})
        return {'status': 'ok' if error is None else 'cancelled' if cancelled else 'error',
                'translation': ''.join(output), 'usage': normalized, 'error': error,
                'finish_reason': terminal, 'elapsed_seconds': time.monotonic() - started,
                'ttft_seconds': first_text, 'generation_attempted': submitted,
                'completion_known': terminal is not None, 'request_id': request_id, 'response_id': response_id,
                'timing': {'auth_ready_seconds': request_started, 'headers_seconds': headers_at,
                           'first_event_seconds': first_event, 'first_text_seconds': first_text},
                'event_counts': event_counts, 'request_sha256': digest(body),
                'actual_configuration': {'requested_model': self.model, 'requested_reasoning_effort': self.reasoning_effort,
                    'requested_service_tier': 'default', 'reported': actual, 'protocol': 'responses_http_sse',
                    'store': False, 'stream': True, 'automatic_retries': 0},
                'base_instructions_sha256': digest(self.instructions.encode())}

    def close(self):
        self.client.close()
        self.store.client.close()
