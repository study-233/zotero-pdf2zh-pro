"""Explicit provider adapters. Importing this module never starts a model."""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from .common import ROOT, digest, file_hash, read


def cache_seed_identity(config):
    if config.get('cache', 'cold') != 'warm':
        return None
    root = Path(config.get('cache_seed', '')).expanduser()
    if not root.is_dir() or not (root / 'run.json').is_file():
        raise ValueError('Warm cache requires cache_seed pointing to a completed product run')
    manifest = read(root / 'run.json')
    if manifest.get('status') != 'completed' or manifest['identity']['suite'] not in {'selection', 'pdf-quick', 'pdf-full'}:
        raise ValueError('Cache seed must be a completed selection/PDF run')
    seed_config = manifest['identity']['provider']
    for key in ('provider', 'model', 'base_url', 'protocol', 'reasoning_effort', 'product_request_options', 'pdf_options'):
        # ChatGPT has one fixed wire protocol; recorded identity makes the
        # default explicit even when the operator's config omits it.
        value = config.get(key, 'responses' if key == 'protocol' and config['provider'] == 'chatgpt' else None)
        if seed_config.get(key) != value:
            raise ValueError(f'Cache seed model/config mismatch: {key}')
    files = [p for p in (root / 'runtime' / 'cache').rglob('*') if p.is_file()]
    files += [root / 'runtime' / 'tasks' / name for name in ('selection-cache.sqlite3', 'translation-memory.sqlite3') if (root / 'runtime' / 'tasks' / name).is_file()]
    if not files or any(p.is_symlink() for p in files) or any(p.name.endswith(('-wal', '-shm')) for p in files):
        raise ValueError('Cache seed must be quiescent, nonempty and contain no symlinks or open SQLite WAL')
    return {'run_id': manifest['run_id'], 'dataset_sha256': manifest['identity']['dataset_sha256'],
            'files': {str(p.relative_to(root / 'runtime')): file_hash(p) for p in sorted(files)}}


def usage(raw):
    raw = raw or {}
    def number(value):
        return value if type(value) is int and value >= 0 else None
    incoming = number(raw.get('input_tokens', raw.get('prompt_tokens')))
    outgoing = number(raw.get('output_tokens', raw.get('completion_tokens')))
    details = raw.get('input_tokens_details') or raw.get('prompt_tokens_details') or {}
    output_details = raw.get('output_tokens_details') or raw.get('completion_tokens_details') or {}
    cached = number(details.get('cached_tokens', raw.get('cache_hit_tokens')))
    reasoning = number(output_details.get('reasoning_tokens', raw.get('reasoning_tokens')))
    return {'input': incoming, 'output': outgoing, 'cached_input': cached, 'reasoning': reasoning,
            'availability': 'complete' if incoming is not None and outgoing is not None else 'partial' if incoming is not None or outgoing is not None else 'unavailable'}


def validate_config(config):
    if config.get('provider') not in {'codex', 'chatgpt', 'openai', 'lmstudio'} or not isinstance(config.get('model'), str) or not config['model'].strip():
        raise ValueError('provider (codex/chatgpt/openai/lmstudio) and model are required')
    forbidden = {'api_key', 'apiKey', 'authorization', 'headers'}
    if forbidden & set(config):
        raise ValueError('Use api_key_env; do not store credentials in benchmark configuration')
    def inspect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key.lower().replace('_', '') in {'apikey', 'authorization', 'password', 'cookie', 'headers', 'extraheaders'}:
                    raise ValueError('Credentials/headers must not be persisted in benchmark configuration')
                inspect(item)
        elif isinstance(value, list):
            for item in value:
                inspect(item)
    inspect(config)
    limits = config.get('limits', {})
    for key in ('max_requests', 'max_seconds'):
        if type(limits.get(key)) is not int or limits[key] <= 0:
            raise ValueError(f'limits.{key} must be a positive integer')
    if 'max_estimated_usd' in limits:
        limit = limits['max_estimated_usd']
        if type(limit) not in (int, float) or not math.isfinite(limit) or limit <= 0 or config['provider'] in ('codex', 'chatgpt'):
            raise ValueError('Estimated cost limit requires a positive finite value and a metered provider')
        if not config.get('prices_per_million'):
            raise ValueError('Estimated cost limits require an archived price snapshot')
    if config.get('prices_per_million'):
        prices = config['prices_per_million']
        if not prices.get('source') or not prices.get('as_of') or any(type(prices.get(k)) not in (int, float) or not math.isfinite(prices[k]) or prices[k] < 0 for k in ('input', 'output', 'cached_input')):
            raise ValueError('Prices require finite rates, source and as_of')
    if 'timeout_seconds' in config and (type(config['timeout_seconds']) not in (int, float) or not math.isfinite(config['timeout_seconds']) or config['timeout_seconds'] <= 0):
        raise ValueError('timeout_seconds must be finite and positive')
    if config.get('provider') not in ('codex', 'chatgpt'):
        endpoint = urlsplit(config.get('base_url', ''))
        if endpoint.scheme not in {'http', 'https'} or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
            raise ValueError('base_url must be an HTTP(S) endpoint without credentials/query/fragment')
        if config.get('protocol', 'chat_completions') not in {'chat_completions', 'responses', 'completions', 'anthropic_messages'}:
            raise ValueError('Choose an explicit protocol; protocol fallback is disabled')
    if config['provider'] == 'chatgpt' and not config.get('auth_directory'):
        raise ValueError('ChatGPT plan requires its own auth_directory')
    if config['provider'] == 'chatgpt' and (config.get('parameters') or config.get('base_url') or config.get('prices_per_million')):
        raise ValueError('ChatGPT plan uses the fixed official route and has no token-based price estimate')
    parameters = config.get('parameters', {})
    if set(parameters) - {'temperature', 'max_tokens', 'seed', 'reasoning_effort', 'top_p'}:
        raise ValueError('Unsupported or reserved model parameter')
    for key in ('temperature', 'top_p'):
        if key in parameters and (type(parameters[key]) not in (int, float) or not math.isfinite(parameters[key]) or parameters[key] < 0):
            raise ValueError(f'Invalid {key}')
    if 'max_tokens' in parameters and (type(parameters['max_tokens']) is not int or parameters['max_tokens'] <= 0):
        raise ValueError('max_tokens must be positive')
    if set(config.get('pdf_options', {})) - {'qps', 'poolSize', 'disableTermExtraction', 'semanticReview', 'translateTableText', 'skipReferences', 'autoOcr', 'ocr', 'fontFamily'}:
        raise ValueError('Unsupported or reserved PDF option')
    if config.get('cache', 'cold') not in {'cold', 'warm'}:
        raise ValueError('cache must be cold or warm')
    cache_seed_identity(config)
    if config.get('provider') == 'lmstudio':
        if urlsplit(config['base_url']).hostname not in {'127.0.0.1', 'localhost', '::1'}:
            raise ValueError('LM Studio lifecycle requires a loopback endpoint')
        for key in ('model_file', 'quantization', 'context_length', 'chat_template_sha256'):
            if not config.get(key):
                raise ValueError(f'LM Studio requires {key}')
    return config


def provider_identity(config):
    result = dict(config)
    if config.get('cache') == 'warm':
        result['cache_seed_identity'] = cache_seed_identity(config)
    if config['provider'] == 'codex':
        path = config.get('cli_path') or shutil.which('codex')
        if not path:
            raise ValueError('Codex CLI not found')
        result['cli_version'] = subprocess.check_output([path, '--version'], text=True, timeout=10).strip()
        result['client_sha256'] = file_hash(ROOT / 'server' / 'codex_client.py')
        result['uncontrolled'] = ['temperature', 'context_length', 'max_output_tokens', 'provider_internal_retries']
    if config['provider'] == 'chatgpt':
        result['protocol'] = 'responses'
        result['client_sha256'] = file_hash(ROOT / 'server' / 'chatgpt_client.py')
        if config.get('experiment_transport') == 'chatgpt_connection_probe':
            loaded_hash = file_hash(Path(config['client_file']))
            if loaded_hash != config['client_sha256']:
                raise ValueError('Frozen benchmark client changed')
            result['loaded_client_sha256'] = loaded_hash
        result['uncontrolled'] = ['provider_cache', 'provider_internal_retries', 'subscription_metering']
    if config['provider'] == 'lmstudio':
        result['model_sha256'] = file_hash(Path(config['model_file']).expanduser())
        if config.get('model_sha256') and result['model_sha256'] != config['model_sha256']:
            raise ValueError('LM Studio model file changed')
        path = config.get('lms_path') or shutil.which('lms')
        if not path:
            raise ValueError('lms CLI not found')
        result['lms_version'] = subprocess.check_output([path, '--version'], text=True, timeout=10).strip()
        result['metadata_provenance'] = {'file_hash': 'measured', 'quantization': 'operator_declared', 'chat_template_sha256': 'operator_declared'}
    return result


class ChatGPTProvider:
    def __init__(self, config):
        sys.path.insert(0, str(ROOT / 'server'))
        from chatgpt_client import ChatGPTClient, INSTRUCTIONS
        self.config, self.base_instructions = config, INSTRUCTIONS
        self.client = ChatGPTClient(config['auth_directory'], account_id=config.get('account_id'))
        self.actual = self.client.check_ready(config['model'], config.get('reasoning_effort'))

    def generate(self, prompt, timeout):
        started, chunks, first, used = time.monotonic(), [], [], {}
        def delta(text):
            if not first: first.append(time.monotonic() - started)
            chunks.append(text)
        try:
            result = self.client.translate(prompt, model=self.config['model'], reasoning_effort=self.config.get('reasoning_effort'),
                timeout=timeout, on_text=delta, on_usage=used.update, allow_auth_retry=False)
            return dict(status='ok', translation=result.text, usage=usage(used), elapsed_seconds=time.monotonic()-started,
                        ttft_seconds=first[0] if first else None, finish_reason='completed', actual_configuration=self.actual)
        except Exception as exc:
            return dict(status='error', translation=''.join(chunks), usage=usage(used), elapsed_seconds=time.monotonic()-started,
                        ttft_seconds=first[0] if first else None, error={'type':type(exc).__name__, 'code':getattr(exc,'code',None)})

    def close(self):
        self.client.close()


class CodexProvider:
    def __init__(self, config):
        sys.path.insert(0, str(ROOT / 'server'))
        from codex_client import CodexClient, INSTRUCTIONS
        self.config, self.base_instructions = config, INSTRUCTIONS
        self.client = CodexClient(cli_path=config.get('cli_path'), proxy_mode=config.get('proxy_mode', 'inherit'))
        self.actual = self.client.check_ready(config['model'], reasoning_effort=config.get('reasoning_effort'))

    def generate(self, prompt, timeout):
        started, chunks, first, used = time.monotonic(), [], [], {}
        def on_text(text):
            if text:
                first.append(time.monotonic() - started) if not first else None
                chunks.append(text)
        status, error, finish = 'ok', None, 'completed'
        try:
            response = self.client.translate(prompt, model=self.config['model'], reasoning_effort=self.config.get('reasoning_effort'),
                                             timeout=timeout, on_text=on_text, on_usage=used.update)
            text = response.text
            used.update(response.usage or {})
        except Exception as exc:
            text, status, finish = ''.join(chunks), 'error', 'failed'
            error = {'type': type(exc).__name__, 'code': getattr(exc, 'code', None)}
        result = {'status': status, 'translation': text, 'usage': usage(used), 'error': error, 'finish_reason': finish,
                  'elapsed_seconds': time.monotonic() - started, 'ttft_seconds': first[0] if first else None,
                  'actual_configuration': self.actual, 'base_instructions_sha256': digest(self.base_instructions.encode())}
        if status != 'ok' and result['usage']['availability'] == 'complete':
            result['usage']['availability'] = 'partial'
        return result

    def close(self):
        self.client.close()


class OpenAIProvider:
    def __init__(self, config):
        import httpx
        self.config, self.owned_model = config, None
        self.model = config['model']
        self.client = httpx.Client(trust_env=urlsplit(config['base_url']).hostname not in {'localhost', '127.0.0.1', '::1'})

    def generate(self, prompt, timeout):
        protocol = self.config.get('protocol', 'chat_completions')
        stream = self.config.get('stream', True)
        anthropic = protocol == 'anthropic_messages'
        endpoint = {'responses': 'responses', 'completions': 'completions', 'chat_completions': 'chat/completions', 'anthropic_messages': 'messages'}[protocol]
        body = {'model': self.model, 'stream': stream}
        body[{'responses': 'input', 'completions': 'prompt', 'chat_completions': 'messages', 'anthropic_messages': 'messages'}[protocol]] = prompt if protocol == 'completions' else [{'role': 'user', 'content': prompt}]
        params = self.config.get('parameters', {'temperature': 0, 'max_tokens': 4096})
        if set(params) - {'temperature', 'max_tokens', 'seed', 'reasoning_effort', 'top_p'}:
            raise ValueError('Unsupported or reserved model parameter')
        for key, value in params.items():
            if protocol == 'responses' and key == 'reasoning_effort':
                body['reasoning'] = {'effort': value}
            else:
                body['max_output_tokens' if protocol == 'responses' and key == 'max_tokens' else key] = value
        if stream and protocol == 'chat_completions':
            body['stream_options'] = {'include_usage': True}
        secret = os.environ.get(self.config.get('api_key_env', ''), '')
        headers = {'Authorization': 'Bearer ' + secret} if secret else {}
        if anthropic:
            headers['anthropic-version'] = '2023-06-01'
        started, chunks, first, raw_usage, finish = time.monotonic(), [], [], {}, None
        message_stopped, response_id, response_model, request_id = False, None, None, None
        status, error = 'ok', None
        def consume(event):
            nonlocal raw_usage, finish, message_stopped, response_id, response_model
            if event.get('error') or event.get('type') in {'error', 'response.failed', 'response.incomplete'}:
                raise ValueError('provider_event_error')
            if anthropic:
                message = event.get('message', event) if event.get('type') == 'message_start' or not stream else {}
                response_id = message.get('id', response_id)
                response_model = message.get('model', response_model)
                raw_usage.update(message.get('usage') or {})
                if event.get('type') == 'message_delta':
                    raw_usage.update(event.get('usage') or {})
                    finish = (event.get('delta') or {}).get('stop_reason') or finish
                delta = ''
                if event.get('type') == 'content_block_delta' and event.get('delta', {}).get('type') == 'text_delta':
                    delta = event['delta']['text']
                if not stream:
                    delta = ''.join(c.get('text', '') for c in message.get('content', []) if c.get('type') == 'text')
                    finish = message.get('stop_reason')
                message_stopped = message_stopped or event.get('type') == 'message_stop' or not stream
                if delta:
                    if not first:
                        first.append(time.monotonic() - started)
                    chunks.append(delta)
                return
            response = event.get('response', event)
            response_id = response.get('id', response_id)
            response_model = response.get('model', response_model)
            if response.get('usage'):
                raw_usage = response['usage']  # cumulative snapshots replace, never add
            if protocol == 'responses':
                delta = event.get('delta', '') if event.get('type') == 'response.output_text.delta' else ''
                if event.get('type') == 'response.completed' or not stream:
                    finish = response.get('status')
                    if not stream:
                        delta = ''.join(c.get('text', '') for o in response.get('output', []) if o.get('type') == 'message' for c in o.get('content', []) if c.get('type') == 'output_text')
            else:
                choices = response.get('choices') or []
                choice = next((c for c in choices if c.get('index', 0) == 0), {})
                delta = choice.get('text', '') if protocol == 'completions' else ((choice.get('delta') if stream else choice.get('message')) or {}).get('content', '')
                finish = choice.get('finish_reason') or finish
            if delta:
                if not first:
                    first.append(time.monotonic() - started)
                chunks.append(delta)
        try:
            with self.client.stream('POST', self.config['base_url'].rstrip('/') + '/' + endpoint, json=body, headers=headers, timeout=timeout) as response:
                request_id = response.headers.get('x-request-id') or response.headers.get('request-id')
                if response.status_code >= 400:
                    status, error = 'error', {'type': 'HTTPError', 'status_code': response.status_code}
                elif not stream:
                    consume(json.loads(response.read()))
                else:
                    for line in response.iter_lines():
                        if time.monotonic() - started >= timeout:
                            raise TimeoutError('deadline')
                        if line.startswith('data:'):
                            value = line[5:].strip()
                            if value and value != '[DONE]':
                                consume(json.loads(value))
            finished = finish in {'end_turn', 'stop_sequence'} and message_stopped if anthropic else finish in {'stop', 'completed'}
            if status == 'ok' and (not finished or not ''.join(chunks).strip()):
                status, error = 'error', {'type': 'IncompleteOutput'}
        except Exception as exc:
            status, error = 'error', {'type': type(exc).__name__}
        normalized_usage = usage(raw_usage)
        if anthropic:
            cached, created = raw_usage.get('cache_read_input_tokens'), raw_usage.get('cache_creation_input_tokens')
            if all(type(n) is int and n >= 0 for n in (normalized_usage['input'], cached, created)):
                normalized_usage['input'] += cached + created
            elif cached is not None or created is not None:
                normalized_usage['availability'] = 'partial'
            normalized_usage.update(cached_input=cached, cache_write_input=created)
        return {'status': status, 'translation': ''.join(chunks), 'usage': normalized_usage, 'error': error,
                'raw_usage': raw_usage, 'request_id': request_id, 'response_id': response_id, 'response_model': response_model,
                'finish_reason': finish, 'elapsed_seconds': time.monotonic() - started,
                'ttft_seconds': first[0] if stream and first else None,
                'actual_configuration': {'requested_model': self.model, 'protocol': protocol, 'parameters': params}}

    def close(self):
        self.client.close()


class LMStudioProvider(OpenAIProvider):
    def __init__(self, config):
        super().__init__(config)
        self.lms = config.get('lms_path') or shutil.which('lms')
        loaded = json.loads(subprocess.check_output([self.lms, 'ps', '--json'], text=True, timeout=15))
        if loaded:
            self.client.close()
            raise ValueError('LM Studio has existing loaded models; use an idle runtime')
        identifier = 'translation-bench-' + uuid.uuid4().hex[:16]
        try:
            subprocess.run([self.lms, 'load', config['model'], '--identifier', identifier,
                            '--context-length', str(config['context_length']), '--parallel', '1',
                            '--gpu', str(config.get('gpu', 'max')), '--yes'], check=True, capture_output=True, timeout=300)
            self.owned_model = self.model = identifier
            self.loaded_configuration = json.loads(subprocess.check_output([self.lms, 'ps', '--json'], text=True, timeout=15))
        except BaseException:
            # Only this unique identifier may have been loaded by a timed-out command.
            subprocess.run([self.lms, 'unload', identifier], capture_output=True, timeout=30)
            self.client.close()
            raise

    def generate(self, prompt, timeout):
        result = super().generate(prompt, timeout)
        result['actual_configuration']['loaded_instances'] = self.loaded_configuration
        result['actual_configuration']['owned_identifier'] = self.owned_model
        return result

    def close(self):
        try:
            if self.owned_model:
                subprocess.run([self.lms, 'unload', self.owned_model], check=True, capture_output=True, timeout=30)
        finally:
            super().close()


def create_provider(config):
    return {'codex': CodexProvider, 'chatgpt': ChatGPTProvider, 'openai': OpenAIProvider, 'lmstudio': LMStudioProvider}[config['provider']](config)
