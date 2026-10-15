"""Exercise the actual selection/PDF HTTP interfaces in an owned local service."""
from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

from .common import ROOT, append, code_identity, digest, environment, file_hash, now, read, rows, write
from .corpus import verify
from .providers import LMStudioProvider, cache_seed_identity, provider_identity, validate_config

TERMINAL = {'completed', 'incomplete', 'failed', 'cancelled'}


def ledger_usage(events, expected_calls=None):
    starts = {e['callId'] for e in events if e['event'] == 'request_started'}
    uses = {e['callId']: e for e in events if e['event'] == 'usage'}
    finishes = {e['callId'] for e in events if e['event'] == 'request_finished'}
    complete = starts == set(uses) == finishes and (expected_calls is None or len(starts) == expected_calls) and all(e.get('complete') and type(e.get('input')) is int and type(e.get('output')) is int for e in uses.values())
    known_zero = not starts and expected_calls == 0
    return {'input': sum(e['input'] for e in uses.values() if type(e.get('input')) is int) if any(type(e.get('input')) is int for e in uses.values()) else 0 if known_zero else None,
            'output': sum(e['output'] for e in uses.values() if type(e.get('output')) is int) if any(type(e.get('output')) is int for e in uses.values()) else 0 if known_zero else None,
            'cached_input': sum(e['cachedInput'] for e in uses.values() if type(e.get('cachedInput')) is int) if uses and all(type(e.get('cachedInput')) is int for e in uses.values()) else 0 if known_zero else None,
            'reasoning': sum(e['reasoning'] for e in uses.values() if type(e.get('reasoning')) is int) if any(type(e.get('reasoning')) is int for e in uses.values()) else None,
            'availability': 'complete' if complete and (starts or known_zero) else 'partial' if uses else 'unavailable', 'calls': len(starts)}


def seed_cache(config, runtime):
    identity = cache_seed_identity(config)
    if not identity or (runtime / 'cache-seed.json').exists():
        return
    source = Path(config['cache_seed']).expanduser() / 'runtime'
    for relative in identity['files']:
        destination = runtime / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise ValueError('Refusing to replace an existing runtime cache')
        shutil.copy2(source / relative, destination)
    write(runtime / 'cache-seed.json', identity)


class ProductService:
    def __init__(self, config, run_dir):
        import httpx
        self.config, self.root = config, Path(run_dir) / 'runtime'
        self.root.mkdir(parents=True, exist_ok=True)
        seed_cache(config, self.root)
        self.ledger = self.root / 'ledger'
        self.ledger.mkdir(exist_ok=True)
        self.local_model, self.process, self.log = None, None, None
        self.client = httpx.Client(trust_env=False, timeout=15)
        self.service = config['provider'] if config['provider'] in ('codex', 'chatgpt') else 'openai'
        if self.service in ('codex', 'chatgpt'):
            self.api = {'model': config['model'], 'reasoningEffort': config.get('reasoning_effort', 'low')}
            if config.get('account_id'):
                self.api['accountId'] = config['account_id']
            if config.get('cli_path'):
                self.api['cliPath'] = config['cli_path']
        else:
            if config.get('protocol') == 'completions':
                raise ValueError('Product supports Chat Completions/Responses, not raw completions')
            self.api = {'model': config['model'], 'apiUrl': config['base_url'],
                        'apiKey': os.environ.get(config.get('api_key_env', ''), 'local'),
                        'apiProtocol': config.get('protocol', 'chat_completions'),
                        'requestOptions': config.get('product_request_options', {})}
        try:
            if config['provider'] == 'lmstudio':
                self.local_model = LMStudioProvider(config)
                self.api['model'] = self.local_model.model
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 0))
                port = probe.getsockname()[1]
            self.url = f'http://127.0.0.1:{port}'
            env = dict(os.environ, PDF2ZH_DATA_DIR=str((self.root / 'tasks').resolve()),
                       PDF2ZH_CONFIG_DIR=str((self.root / 'config').resolve()),
                       PDF2ZH_TRANSLATION_CACHE_DIR=str((self.root / 'cache').resolve()),
                       PDF2ZH_BENCHMARK_LEDGER_DIR=str(self.ledger.resolve()),
                       PDF2ZH_BENCHMARK_MAX_REQUESTS=str(config['limits']['max_requests']))
            if config['provider'] == 'chatgpt':
                env['PDF2ZH_CHATGPT_AUTH_DIR'] = str(Path(config['auth_directory']).resolve())
            self.log = (self.root / 'server.log').open('ab')
            entry = [str(ROOT / 'server' / 'server.py')]
            if config.get('experiment_transport') == 'chatgpt_direct':
                if config['provider'] != 'codex':
                    raise ValueError('Direct experiment uses the shared Codex PDF adapter only')
                entry = ['-m', 'benchmarks.translation.direct_service']
                env['PYTHONPATH'] = str(ROOT) + os.pathsep + env.get('PYTHONPATH', '')
                env['PDF2ZH_EXPERIMENT_DIRECT_AUTH'] = str(Path(config['experiment_auth_directory']).resolve())
                env['PDF2ZH_EXPERIMENT_DIRECT_LEDGER'] = str((self.root / 'direct-ledger').resolve())
            elif config.get('experiment_transport') == 'chatgpt_connection_probe':
                if config['provider'] != 'chatgpt':
                    raise ValueError('Connection probe requires the ChatGPT product adapter')
                entry = ['-m', 'benchmarks.translation.connection_service']
                env['PYTHONPATH'] = str(ROOT) + os.pathsep + env.get('PYTHONPATH', '')
                env['PDF2ZH_BENCHMARK_CLIENT_FILE'] = str(Path(config['client_file']).resolve())
                env['PDF2ZH_BENCHMARK_CLIENT_SHA256'] = config['client_sha256']
                env['PDF2ZH_BENCHMARK_CONNECTION_TRACE'] = str((self.root / 'connection-trace').resolve())
                if 'experimental_worker_cap' in config:
                    if config['experimental_worker_cap'] != 4:
                        raise ValueError('Only the bounded four-worker experiment is supported')
                    env['PDF2ZH_BENCHMARK_WORKER_CAP'] = '4'
            self.process = subprocess.Popen([config.get('server_python', sys.executable), *entry,
                                             '--host', '127.0.0.1', '--port', str(port)], cwd=ROOT / 'server', env=env,
                                            stdout=self.log, stderr=subprocess.STDOUT, start_new_session=os.name != 'nt')
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise RuntimeError('Benchmark service exited; inspect runtime/server.log')
                try:
                    health = self.client.get(self.url + '/health', timeout=1).json()
                    if Path(health.get('workspace', {}).get('path', '')).resolve() != (self.root / 'tasks').resolve():
                        raise ValueError('Refusing a service using a different task workspace')
                    self.health = health
                    break
                except httpx.HTTPError:
                    time.sleep(.2)
            else:
                raise TimeoutError('Benchmark service did not become ready')
        except BaseException:
            self.close()
            raise

    def events(self):
        return [event for path in sorted(self.ledger.glob('*.jsonl')) for event in rows(path)]

    def generate_sample(self, sample, timeout):
        started, output, first, status, error = time.monotonic(), [], None, 'error', None
        before = {e.get('callId') for e in self.events() if e.get('callId')}
        request_id = 'bench_' + uuid.uuid4().hex
        payload = {'text': sample['source_text'], 'context': sample.get('selection_context', ''),
                   'documentFingerprint': digest((sample.get('paper') or sample['id']).encode()),
                   'mode': 'translate', 'source': 'en', 'target': 'zh-CN', 'memoryPolicy': 'exact',
                   'cachePolicy': 'refresh' if self.config.get('cache', 'cold') == 'cold' else 'prefer',
                   'service': self.service, 'llm_api': self.api, 'requestId': request_id,
                   'glossaryEntries': sample.get('selection_glossary', [])}
        metadata, event_name = {}, ''
        try:
            with self.client.stream('POST', self.url + '/translate-text/stream', json=payload, timeout=timeout) as response:
                if response.status_code >= 400:
                    raise RuntimeError(f'HTTP_{response.status_code}')
                for line in response.iter_lines():
                    if time.monotonic() - started >= timeout:
                        raise TimeoutError()
                    if line.startswith('event:'):
                        event_name = line[6:].strip()
                    elif line.startswith('data:'):
                        value = json.loads(line[5:])
                        if event_name == 'delta':
                            text = value.get('text', '')
                            if text:
                                first = time.monotonic() - started if first is None else first
                                output.append(text)
                        elif event_name in {'done', 'result'}:
                            metadata = value
                            output = [value.get('translation', ''.join(output))]
                            status = 'ok'
                        elif event_name == 'error':
                            error = {'type': 'ProductError', 'code': value.get('code')}
        except Exception as exc:
            error = {'type': type(exc).__name__}
            try:
                self.client.post(self.url + '/cancel-text', json={'requestId': request_id}, timeout=5)
            except Exception:
                pass
        events = [e for e in self.events() if e.get('callId') and e['callId'] not in before]
        used = ledger_usage(events, 0 if metadata.get('cached') else None)
        if not used['calls'] and not metadata.get('cached'):
            used.update(input=None, output=None, availability='unavailable')
        return {'status': status, 'translation': ''.join(output), 'error': error, 'finish_reason': 'completed' if status == 'ok' else 'failed',
                'elapsed_seconds': time.monotonic() - started, 'ttft_seconds': first, 'usage': used,
                'product_result': metadata, 'call_ids': sorted({e['callId'] for e in events}),
                'cache_source': ('selection-cache' if metadata.get('provider') == self.service
                                 else metadata.get('provider', 'unknown-cache')) if metadata.get('cached') else None}

    def reconcile(self, filename):
        response = self.client.get(self.url + '/tasks')
        response.raise_for_status()
        matches = [t for t in response.json().get('tasks', []) if t.get('fileName') == filename]
        if len(matches) > 1:
            raise ValueError('Ambiguous task submission; manual inspection required')
        return matches[0] if matches else None

    def close(self):
        try:
            if self.process is not None and self.process.poll() is None:
                import psutil
                parent = psutil.Process(self.process.pid)
                children = parent.children(recursive=True)
                self.process.terminate()
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
                for child in children:
                    try:
                        child.terminate()
                    except psutil.NoSuchProcess:
                        pass
                _, alive = psutil.wait_procs(children, timeout=2)
                for child in alive:
                    child.kill()
        finally:
            self.client.close()
            if self.log:
                self.log.close()
            if self.local_model:
                self.local_model.close()


def pdf_jobs(data, suite):
    sources = read(Path(data) / 'sources.json')
    if suite == 'pdf-full':
        return [{'paper': s['id'], 'page': None} for s in sources]
    return read(Path(data) / 'manifest.json')['pdf_quick']


def run_pdf(data, config, suite, output, *, resume=False):
    import fitz
    verified = verify(data)
    validate_config(config)
    output, data = Path(output), Path(data)
    jobs = pdf_jobs(data, suite)
    sources = {s['id']: s for s in read(data / 'sources.json')}
    identity = {'dataset_sha256': verified['content_sha256'], 'suite': suite, 'provider': provider_identity(config),
                'prompt_version': 'product-pdf', 'code': code_identity(), 'environment': environment(),
                'cache': config.get('cache', 'cold'), 'concurrency': 1}
    seed = identity['provider'].get('cache_seed_identity')
    if seed and seed['dataset_sha256'] != verified['content_sha256']:
        raise ValueError('Cache seed dataset mismatch')
    fingerprint = digest(identity)
    job_ids = [f'main:{j["paper"]}-{j["page"] or "full"}:0' for j in jobs]
    if resume:
        manifest = read(output / 'run.json')
        if manifest['fingerprint'] != fingerprint:
            raise ValueError('PDF resume configuration drift')
        manifest['resumed'] = True
    else:
        if output.exists():
            raise ValueError('Output exists')
        output.mkdir(parents=True)
        manifest = {'schema': 2, 'run_id': uuid.uuid4().hex, 'created_at': now(), 'identity': identity,
                    'fingerprint': fingerprint, 'planned_jobs': job_ids, 'status': 'running', 'elapsed_seconds': 0}
        write(output / 'run.json', manifest)
        write(output / 'samples.json', {j.rsplit(':', 1)[0][5:]: {'source_text': sources[v['paper']]['title'],
               'domain': sources[v['paper']]['domain'], 'origin': 'pdf', 'length': 'document', 'split': sources[v['paper']]['split'],
               'group': v['paper'], 'anchors': sources[v['paper']]['anchors'] if not v['page'] else [{'page': v['page'], 'role': 'quick'}]} for j, v in zip(job_ids, jobs)})
        write(output / 'rubric.json', read(data / 'rubric.json'))
    from .runner import _lock
    with (output / '.lock').open('a+b') as lock:
        _lock(lock)
        done = {r['job_id'] for r in rows(output / 'results.jsonl')}
        service, started = None, time.monotonic()
        try:
            service = ProductService(config, output)
            from .runner import estimate_cost
            rates, cost_limit = config.get('prices_per_million'), config['limits'].get('max_estimated_usd')
            def cost_stop():
                if cost_limit is None:
                    return False
                events = service.events()
                # In-flight calls have no final usage yet; apply the soft limit to finished calls.
                finished = {e['callId'] for e in events if e['event'] == 'request_finished'}
                amount = estimate_cost({'usage': ledger_usage([e for e in events if e.get('callId') in finished], len(finished))}, rates)
                return amount is None or amount >= cost_limit
            for job, job_id in zip(jobs, job_ids):
                if job_id in done:
                    continue
                if cost_stop():
                    manifest['status'] = 'cost_limit_or_unknown_usage'
                    break
                remaining = config['limits']['max_seconds'] - manifest['elapsed_seconds'] - (time.monotonic() - started)
                if remaining <= 0:
                    manifest['status'] = 'budget_exhausted'
                    break
                case_start = time.monotonic()
                sample_id = job_id.split(':')[1]
                state_path = output / 'submissions' / (sample_id + '.json')
                if state_path.exists():
                    submission = read(state_path)
                    task = service.reconcile(submission['file_name'])
                    if task is None:
                        raise ValueError('Uncertain PDF submission has no matching task; refusing automatic resubmission')
                else:
                    filename = 'bench-' + manifest['run_id'][:12] + '-' + sample_id + '.pdf'
                    pdf = data / sources[job['paper']]['pdf']
                    if job['page']:
                        with fitz.open(pdf) as original, fitz.open() as excerpt:
                            excerpt.insert_pdf(original, from_page=job['page'] - 1, to_page=job['page'] - 1)
                            content = excerpt.tobytes()
                    else:
                        content = pdf.read_bytes()
                    submission = {'file_name': filename, 'created_at': now(), 'state': 'submit_intent'}
                    write(state_path, submission)
                    payload = {'fileName': filename, 'fileContent': base64.b64encode(content).decode(),
                               'service': service.service, 'llm_api': service.api, 'sourceLang': 'en', 'targetLang': 'zh-CN',
                               'outputModes': ['mono', 'dual'], 'qps': 2, 'poolSize': 2,
                               'disableTermExtraction': True, 'semanticReview': False, 'translateTableText': True,
                               'skipReferences': False, **config.get('pdf_options', {})}
                    # A lost POST response must only be reconciled, never blindly sent again.
                    try:
                        response = service.client.post(service.url + '/tasks', json=payload, timeout=30)
                        response.raise_for_status()
                        task = response.json()['task']
                    except Exception:
                        task = service.reconcile(filename)
                        if task is None:
                            raise ValueError('Uncertain PDF submission; inspect the service before resuming') from None
                    write(state_path, {**submission, 'state': 'submitted', 'task_id': task['taskId']})
                task_id = task['taskId']
                while task['status'] not in TERMINAL:
                    if time.monotonic() - case_start >= remaining or cost_stop():
                        service.client.post(service.url + f'/tasks/{task_id}/cancel', timeout=5)
                        deadline = time.monotonic() + 15
                        while time.monotonic() < deadline and task['status'] not in TERMINAL:
                            time.sleep(.25)
                            task = service.client.get(service.url + f'/tasks/{task_id}').json()['task']
                        break
                    time.sleep(.5)
                    task = service.client.get(service.url + f'/tasks/{task_id}').json()['task']
                write(output / 'tasks' / (task_id + '.json'), task)
                artifacts = []
                for mode in task.get('resultFiles', {}):
                    response = service.client.get(service.url + f'/tasks/{task_id}/result', params={'mode': mode}, timeout=60)
                    response.raise_for_status()
                    path = output / 'pdfs' / f'{sample_id}-{mode}.pdf'
                    path.parent.mkdir(exist_ok=True)
                    path.write_bytes(response.content)
                    with fitz.open(path) as document:
                        artifacts.append({'mode': mode, 'path': str(path.relative_to(output)), 'sha256': file_hash(path),
                                          'pages': len(document), 'text_chars': sum(len(p.get_text()) for p in document)})
                events = rows(service.ledger / (task_id + '.jsonl'))
                first_paragraph = [e['timestamp'] for e in events if e['event'] == 'first_paragraph_completed' and e.get('timestamp') is not None]
                row = {'job_id': job_id, 'sample_id': sample_id, 'phase': 'main', 'repeat': 0,
                       'status': 'ok' if task['status'] == 'completed' and len(artifacts) == 2 else 'error',
                       'task_status': task['status'], 'translation': '', 'artifacts': artifacts,
                       'elapsed_seconds': time.monotonic() - case_start, 'ttft_seconds': None,
                       'first_paragraph_seconds': max(0, min(first_paragraph) - datetime.fromisoformat(submission['created_at']).timestamp()) if first_paragraph else None,
                       'usage': ledger_usage(events, (task.get('metrics') or {}).get('requests', {}).get('attempts')), 'task_id': task_id, 'translation_summary': task.get('translationSummary'),
                       'input_pdf': str((service.root / 'tasks' / task_id / submission['file_name']).relative_to(output)),
                       'metrics': task.get('metrics'), 'rules': {'hard_failures': [], 'review_hints': ['PDF anchor review required']}}
                row['estimated_usd'] = estimate_cost(row, rates)
                append(output / 'results.jsonl', row)
                manifest['completed_jobs'] = len(rows(output / 'results.jsonl'))
                write(output / 'run.json', {**manifest, 'updated_at': now()})
                print(f'{sample_id} {task["status"]}', flush=True)
                if config.get('experimental_worker_cap') and (row['status'] != 'ok' or
                        any(e.get('statusCode') == 429 for e in events)):
                    manifest['status'] = 'stopped_after_pdf_error'
                    break
            else:
                manifest['status'] = 'completed'
        finally:
            manifest['elapsed_seconds'] += time.monotonic() - started
            manifest['updated_at'] = now()
            write(output / 'run.json', manifest)
            if service:
                service.close()
    return manifest
