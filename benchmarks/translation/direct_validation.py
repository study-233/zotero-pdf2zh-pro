"""Bounded real-PDF pairs and SIWC lifecycle checks, never baseline promotion."""
from __future__ import annotations

import json
from pathlib import Path
import threading
import time
import uuid

from .chatgpt_auth import AuthStore, DEFAULT_AUTH
from .common import append, code_identity, digest, environment, now, read, rows, write
from .product import ProductService, run_pdf
from .providers import CodexProvider


def lifecycle(config, data, output):
    auth = AuthStore(config['experiment_auth_directory'])
    before = auth.read()
    report = {'started_at': now(), 'expiry_before_seconds': auth.status()['expires_in_seconds']}
    try:
        auth.access_token(force_refresh=True)
        after = auth.read()
        report['refresh'] = {'ok': True, 'access_replaced': before['access_token'] != after['access_token'],
                             'refresh_replaced': before['refresh_token'] != after['refresh_token'],
                             'identity_unchanged': (before['subject'], before['client_id']) == (after['subject'], after['client_id']),
                             'expiry_after_seconds': auth.status()['expires_in_seconds'],
                             'trigger': 'explicit_real_refresh_not_artificial_expiry'}
    finally:
        auth.client.close()
    samples = {r['id']: r for r in rows(Path(data) / 'samples.jsonl')}
    service = ProductService(config, output)
    try:
        report['before_restart'] = service.generate_sample(samples['raft-03'], 90)
        if report['before_restart']['status'] != 'ok':
            raise ValueError('Direct product selection failed before lifecycle test')
        request_id = 'bench_cancel_' + uuid.uuid4().hex
        seen, ended, captured = threading.Event(), threading.Event(), []
        payload = {'text': samples['t5-07']['source_text'], 'mode': 'translate', 'source': 'en', 'target': 'zh-CN',
                   'cachePolicy': 'refresh', 'memoryPolicy': 'exact', 'requestId': request_id,
                   'service': service.service, 'llm_api': service.api}
        def consume():
            event = ''
            try:
                with service.client.stream('POST', service.url + '/translate-text/stream', json=payload, timeout=60) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if line.startswith('event:'):
                            event = line[6:].strip()
                        elif line.startswith('data:'):
                            value = json.loads(line[5:])
                            captured.append({'event': event, 'data': value})
                            if event == 'delta':
                                seen.set()
            except Exception as error:
                captured.append({'exception_type': type(error).__name__})
            finally:
                ended.set()
        worker = threading.Thread(target=consume, daemon=True)
        worker.start()
        if not seen.wait(60):
            service.client.post(service.url + '/cancel-text', json={'requestId': request_id}, timeout=5)
            raise ValueError('Cancellation probe did not receive its first delta')
        started = time.monotonic()
        response = service.client.post(service.url + '/cancel-text', json={'requestId': request_id}, timeout=5)
        response.raise_for_status()
        stopped = ended.wait(10)
        report['cancel'] = {'http_status': response.status_code, 'stream_stopped': stopped,
                            'seconds_to_stream_end': time.monotonic()-started, 'events': captured,
                            'remote_execution_stopped': 'not_confirmed'}
        if not stopped:
            raise ValueError('Cancellation probe did not stop its local stream within 10 seconds')
        # Allow the owned service to finish journaling the cancelled request.
        deadline = time.monotonic()+5
        while time.monotonic()<deadline:
            events = service.events()
            starts = {e['callId'] for e in events if e['event']=='request_started'}
            finishes = {e['callId'] for e in events if e['event']=='request_finished'}
            if starts == finishes: break
            time.sleep(.1)
    finally:
        service.close()
        write(Path(output) / 'lifecycle.json', report)
    service = ProductService(config, output)
    try:
        report['after_restart'] = service.generate_sample(samples['raft-03'], 90)
        report['restart_ok'] = report['after_restart']['status'] == 'ok' and report['after_restart']['usage']['calls'] > 0
        report['product_events'] = service.events()
        report['finished_at'] = now()
        write(Path(output) / 'lifecycle.json', report)
        if not report['restart_ok']:
            raise ValueError('Direct product request failed after process restart')
    finally:
        service.close()
    return report


def validate(data, output, *, cli_path, auth_directory=DEFAULT_AUTH, model='gpt-6-luna', effort='low',
             rounds=3, max_requests=260, max_seconds=5400, execute=False):
    if rounds not in (1,2,3) or max_requests not in range(16,401) or not 60 <= max_seconds <= 7200:
        raise ValueError('Invalid validation limits')
    if not execute:
        return {'executes_model': False, 'pdf_pages_per_route': 4*rounds, 'max_requests': max_requests,
                'max_seconds': max_seconds, 'lifecycle': ['real_refresh','selection_cancel','service_restart']}
    output = Path(output)
    if output.exists():
        raise ValueError('Choose a new validation directory; never replay ambiguous calls')
    output.mkdir(parents=True)
    manifest = {'schema': 1, 'created_at': now(), 'status': 'running', 'code': code_identity(),
        'environment': environment(), 'model': model, 'effort': effort, 'rounds': rounds,
        'limits': {'max_requests': max_requests, 'max_seconds': max_seconds}, 'baseline_promoted': False,
        'product_default_changed': False, 'scope': 'Owned service provider injection; real PDF pipeline; no plugin UI integration'}
    write(output/'validation.json', manifest)
    base = {'provider':'codex', 'model':model, 'reasoning_effort':effort, 'cli_path':cli_path,
            'timeout_seconds':120, 'cache':'cold', 'limits':{'max_requests':60,'max_seconds':1800},
            'pdf_options':{'qps':2,'poolSize':2,'disableTermExtraction':True,'semanticReview':False,
                           'translateTableText':True,'skipReferences':False}}
    direct = {**base, 'experiment_transport':'chatgpt_direct', 'experiment_auth_directory':str(Path(auth_directory).resolve())}
    started, total_calls = time.monotonic(), 0
    try:
        auth, codex = AuthStore(auth_directory), CodexProvider(base)
        try:
            account = codex.client._ensure_started().call('account/read', {'refreshToken':False}).get('account') or {}
            own = auth.read().get('email')
            manifest['account_email_match'] = isinstance(own,str) and isinstance(account.get('email'),str) and own.casefold()==account['email'].casefold()
            if not manifest['account_email_match']:
                raise ValueError('Same account could not be confirmed')
            manifest['auth_fingerprint'] = auth.status()['account_fingerprint']
        finally:
            auth.client.close(); codex.close()
        print('Lifecycle: real refresh, cancellation, restart', flush=True)
        life = lifecycle({**direct, 'limits':{'max_requests':8,'max_seconds':240}}, data, output/'lifecycle')
        total_calls += len({e['callId'] for e in life['product_events'] if e['event']=='request_started'})
        print(json.dumps({'lifecycle':'completed','calls':total_calls,'cancel_seconds':life['cancel']['seconds_to_stream_end']},ensure_ascii=False),flush=True)
        for repeat in range(1,rounds+1):
            for route in (('direct','codex') if repeat%2 else ('codex','direct')):
                remaining = max_seconds-(time.monotonic()-started)
                if remaining <= 0 or total_calls >= max_requests:
                    raise ValueError('Validation execution budget exhausted')
                config = dict(direct if route=='direct' else base)
                config['limits'] = {'max_requests':min(60,max_requests-total_calls),'max_seconds':min(1800,int(remaining))}
                folder = output/f'{repeat:02d}-{route}'
                write(output/'configs'/f'{repeat:02d}-{route}.json',config)
                print(json.dumps({'round':repeat,'route':route,'phase':'pdf-quick','state':'starting'}),flush=True)
                run = run_pdf(data,config,'pdf-quick',folder)
                results = rows(folder/'results.jsonl')
                total_calls += sum(r['usage']['calls'] for r in results)
                append(output/'runs.jsonl', {'round':repeat,'route':route,'path':folder.name,'status':run['status'],
                    'calls':sum(r['usage']['calls'] for r in results), 'seconds':sum(r['elapsed_seconds'] for r in results)})
                write(output/'validation.json',{**manifest,'recorded_calls':total_calls,'updated_at':now()})
                if run['status']!='completed' or len(results)!=4 or any(r['status']!='ok' for r in results):
                    raise ValueError('PDF run did not fully complete; inspect before making further calls')
        manifest['status']='completed'
    except (Exception,KeyboardInterrupt) as error:
        manifest['status']='incomplete'
        manifest['error']={'type':type(error).__name__, 'message':str(error) if type(error) is ValueError else None}
        raise
    finally:
        total_calls = len({e['callId'] for path in output.glob('**/runtime/ledger/*.jsonl')
                           for e in rows(path) if e['event']=='request_started'})
        manifest.update(recorded_calls=total_calls,elapsed_seconds=time.monotonic()-started,finished_at=now())
        write(output/'validation.json',manifest)
    return manifest
