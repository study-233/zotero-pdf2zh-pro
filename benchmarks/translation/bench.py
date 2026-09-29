"""Reproducible PDF benchmark. Paid work is only performed by the explicit run command."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import random
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
DEFAULT_WORK = ROOT / '.local-dev' / 'translation-bench'
TERMINAL = {'completed', 'incomplete', 'failed', 'cancelled'}
RUBRIC = {'fidelity': 40, 'completeness': 20, 'terminology': 20, 'fluency': 10, 'preservation': 10}


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def client(local=False):
    import truststore
    truststore.inject_into_ssl()
    import httpx
    return httpx.Client(timeout=60, follow_redirects=False, trust_env=not local)


def checked(response):
    # Never include upstream bodies, headers or credentials in exceptions/logs.
    if not response.is_success:
        raise RuntimeError(f'HTTP {response.status_code}')
    return response


def cost(metrics, rates):
    tokens = metrics.get('tokens', {})
    if tokens.get('availability') != 'complete':
        return None
    incoming, outgoing = tokens.get('input'), tokens.get('output')
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0 for v in [incoming, outgoing]):
        return None
    cache = metrics.get('providerCache', {})
    hits = cache.get('hitTokens') if cache.get('availability') == 'complete' else 0
    hits = min(incoming, max(0, hits or 0))
    # Reasoning is a subset of completion tokens, never add it a second time.
    return ((incoming - hits) * rates['input'] + hits * rates['cacheRead'] + outgoing * rates['output']) / 1_000_000


def request_rates(model, prompt_tokens, timestamp):
    details = model.get('priceDetails', {})
    rates = {k: model[k] for k in ('input', 'output', 'cacheRead')}
    tiers = details.get('tiers', [])
    for tier in tiers:
        context = tier.get('context', '')
        boundary = re.search(r'(\d+)K', context)
        if not boundary or (prompt_tokens <= int(boundary[1])*1000 if context.startswith('≤') else prompt_tokens > int(boundary[1])*1000):
            rates.update(tier['rates'])
            break
    if details.get('timeOfDay'):
        dt = datetime.fromtimestamp(timestamp, timezone.utc)
        peak = dt.weekday() < 5 and (1 <= dt.hour < 4 or 6 <= dt.hour < 10)
        rates.update(details['timeOfDay']['peak' if peak else 'offPeak'])
    return rates


def ledger_cost(work, task_id, model, metrics):
    path = work / 'usage' / (task_id + '.jsonl')
    if not path.exists():
        return None
    total = 0
    inputs = outputs = 0
    rejected = 0
    for line in path.read_text(encoding='utf-8').splitlines():
        event = json.loads(line)
        if event['input'] is None or event['output'] is None:
            rejected += 1
            continue
        inputs += event['input']
        outputs += event['output']
        hit = event['hit']
        if hit is None and event['miss'] is not None:
            hit = max(0, event['input'] - event['miss'])
        total += cost({'tokens': {'input': event['input'], 'output': event['output'], 'availability': 'complete'},
                       'providerCache': {'hitTokens': hit, 'availability': 'complete' if hit is not None else 'unavailable'}},
                      request_rates(model, event['input'], event['timestamp']))
    tokens = metrics.get('tokens', {})
    requests = metrics.get('requests', {})
    # Explicit authentication rejections have no generated response usage. This
    # is a token-based estimate, never a claim about the provider's actual bill.
    auth_rejections = sum(requests.get('statusCodes', {}).get(str(code), 0) for code in (401, 403))
    if rejected and rejected != auth_rejections:
        return None
    if inputs != (tokens.get('input') or 0) or outputs != (tokens.get('output') or 0):
        return None
    if not rejected and tokens.get('availability') != 'complete':
        return None
    return total


def setup_cost(work, snapshot):
    if 'setupKnownUsd' in snapshot:
        return snapshot['setupKnownUsd']
    total = 0
    for filename in ('access.json', 'diagnostic-access.json'):
        path = work / filename
        if not path.exists():
            continue
        for row in read(path):
            usage = row.get('usage')
            if not usage:
                continue  # Rejected/unknown calls remain disclosed separately.
            model = next(m for m in snapshot['models'] if m['id'] == row['model'])
            prompt, output = usage.get('prompt_tokens'), usage.get('completion_tokens')
            cached = (usage.get('prompt_tokens_details') or {}).get('cached_tokens')
            timestamp = datetime.fromisoformat(row['timestamp']).timestamp()
            amount = cost({'tokens': {'input': prompt, 'output': output, 'availability': 'complete'},
                           'providerCache': {'availability': 'complete' if cached is not None else 'unavailable', 'hitTokens': cached}},
                          request_rates(model, prompt or 0, timestamp))
            if amount is None:
                raise ValueError('Probe usage is incomplete')
            total += amount
    return total


def account_budget_bound(work, snapshot, key):
    """Optional conservative account-wide delta, never assigned to a model."""
    config = snapshot.get('accountBudget')
    if not config or not config.get('approved'):
        return None
    with client() as billing:
        response = checked(billing.get('https://api.commandcode.ai/alpha/usage/summary',
            headers={'Authorization': 'Bearer ' + key})).json()
    current = response.get('totalCost')
    baseline = config['baselineUsd']
    previous_path = work / 'billing-bound.json'
    previous = read(previous_path)['totalUsd'] if previous_path.exists() else baseline
    if isinstance(current, bool) or not isinstance(current, (int, float)) or not math.isfinite(current) or current < max(baseline, previous):
        raise RuntimeError('Billing summary unavailable or billing period reset; pause spending')
    delta = current - baseline
    write(previous_path, {'observedAt': now(), 'totalUsd': current, 'deltaUsd': delta,
                          'note': 'Account-wide delta may include unrelated or delayed earlier calls; budget only.'})
    return delta


def prepare(work):
    import fitz
    manifest = read(HERE / 'manifest.json')
    work.mkdir(parents=True, exist_ok=True)
    if (work / 'snapshot.json').exists():
        snapshot = read(work / 'snapshot.json')
        for paper in snapshot['papers']:
            if digest(work / 'papers' / (paper['id'] + '.pdf')) != paper['sha256']:
                raise ValueError('Frozen paper hash mismatch')
        print('Using frozen snapshot; no inputs were changed.', flush=True)
        return
    with client() as c:
        catalog = checked(c.get(manifest['providerBase'] + '/models')).json()
        write(work / 'models.json', catalog)
        by_id = {m['id']: m for m in catalog['data']}
        for model in manifest['models']:
            entry = by_id.get(model['id'])
            model['catalogAvailable'] = bool(entry and '/chat/completions' in entry.get('supported_endpoints', []))
        for paper in manifest['papers']:
            path = work / 'papers' / (paper['id'] + '.pdf')
            path.parent.mkdir(parents=True, exist_ok=True)
            url = 'https://arxiv.org/pdf/' + paper['arxiv']
            response = checked(c.get(url, follow_redirects=True))
            if not response.content.startswith(b'%PDF'):
                raise ValueError('Paper response is not a PDF')
            path.write_bytes(response.content)
            document = fitz.open(path)
            paper.update(sha256=digest(path), pages=len(document), url=url)
            # Versioned locations and text hashes reproduce the reviewed sample
            # selection without publishing the copyrighted source excerpts.
            anchors = read(HERE / 'sample-anchors.json')[paper['id']]
            selected = []
            for anchor in anchors:
                matches = [b for b in document[anchor['page']-1].get_text('blocks')
                           if hashlib.sha256(b[4].strip().encode()).hexdigest() == anchor['sourceSha256']]
                if len(matches) != 1:
                    raise ValueError('Source anchor mismatch; inspect PDF extraction before testing')
                selected.append({'id':anchor['id'], 'page':anchor['page'], 'bbox':list(matches[0][:4]), 'source':matches[0][4].strip()})
            write(work / 'selection' / (paper['id'] + '.json'), selected)
            paper['selectionSha256'] = digest(work / 'selection' / (paper['id'] + '.json'))
            print(f"Prepared {paper['id']}: {len(document)} pages, {paper['sha256'][:12]}", flush=True)
            document.close()
    manifest.update(preparedAt=now(), commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                    python=sys.version, platform=platform.platform(), rubric=RUBRIC,
                    judge='gpt-6-sol', reviewStatus='pending', zoteroValidation='pending')
    write(work / 'snapshot.json', manifest)


def task_body(pdf, filename, model, snapshot, key):
    return {**snapshot['config'], 'fileName': filename,
            'fileContent': base64.b64encode(pdf).decode(),
            'llm_api': {'service': 'openai', 'name': model['name'], 'model': model['id'],
                        'apiUrl': snapshot['providerBase'] + '/chat/completions', 'apiKey': key,
                        'apiProtocol': 'chat_completions', 'reasoningMode': 'default', 'requestOptions': {}}}


def safe_task(task):
    return {k: task[k] for k in ('taskId', 'status', 'stage', 'createdAt', 'updatedAt',
            'metrics', 'translationSummary', 'canDownloadResult', 'attempt') if k in task}


def recover_submission(c, base, row):
    """A POST with no response may have succeeded: reconcile, never blindly retry."""
    tasks = checked(c.get(base + '/tasks')).json()['tasks']
    matches = [t for t in tasks if t['fileName'] == row['fileName']]
    if len(matches) == 1:
        return matches[0]['taskId']
    raise RuntimeError('Ambiguous submission: inspect isolated service before resuming; no duplicate POST sent')


def run(work, base, pilot=False, paper_filter=None):
    import fitz
    snapshot = read(work / 'snapshot.json')
    if not snapshot['pricingVerified']:
        raise ValueError('Verify snapshot prices and set pricingVerified before paid execution')
    key = os.environ.get('COMMAND_CODE_API_KEY')
    if not key:
        raise ValueError('COMMAND_CODE_API_KEY is required')
    state_path = work / 'runs.json'
    state = read(state_path) if state_path.exists() else []
    config_hash = hashlib.sha256(json.dumps(snapshot['config'], sort_keys=True).encode()).hexdigest()
    if any(r.get('configSha256', config_hash) != config_hash for r in state):
        raise ValueError('Configuration changed: start a separate campaign instead of mixing results')
    with client(local=True) as c:
        health = checked(c.get(base + '/health')).json()
        actual = Path(health['workspace']['path']).resolve()
        if actual != (work / 'tasks').resolve():
            raise ValueError('Refusing non-isolated server: --data-dir must equal work/tasks')
        for paper in snapshot['papers']:
            if paper_filter and paper['id'] != paper_filter:
                continue
            if pilot and paper != snapshot['papers'][0]:
                continue
            path = work / 'papers' / (paper['id'] + '.pdf')
            if digest(path) != paper['sha256']:
                raise ValueError('Frozen PDF has changed')
            if digest(work / 'selection' / (paper['id'] + '.json')) != paper['selectionSha256']:
                raise ValueError('Frozen sample selection has changed')
            models = list(snapshot['models'])
            random.Random(str(snapshot['seed']) + paper['id']).shuffle(models)
            for model in models:
                run_id = paper['id'] + ':' + model['id'] + (':pilot' if pilot else '')
                row = next((r for r in state if r['id'] == run_id), None)
                if row and row.get('status') in TERMINAL | {'unavailable'}:
                    continue
                prior = [r for r in state if r is not row]
                if row is None and any(r.get('status') not in TERMINAL | {'unavailable'} for r in prior):
                    raise RuntimeError('Resume the existing active task before submitting another model')
                account_bound = account_budget_bound(work, snapshot, key)
                if account_bound is None and any(r.get('taskId') and r.get('status') in TERMINAL and r.get('costUsd') is None for r in prior):
                    raise RuntimeError('Unknown previous cost: resolve usage before spending more')
                spent = max(account_bound or 0, setup_cost(work, snapshot) + sum(r.get('costUsd') or 0 for r in prior))
                # Conservative task admission: reserve >= $1 or 3x most expensive prior run.
                reserve = max(1, max((r.get('costUsd') or 0 for r in prior), default=0)*3)
                if not row and spent + reserve > snapshot['budgetUsd'] - snapshot['reserveUsd']:
                    print('Budget admission stopped; no new task submitted.', flush=True)
                    return
                if row is None:
                    row = {'id': run_id, 'paper': paper['id'], 'model': model['id'], 'pilot': pilot,
                           'fileName': f"bench-{paper['id']}-{snapshot['models'].index(model)}{'-pilot' if pilot else ''}.pdf",
                           'status': 'submitting', 'startedAt': now(), 'costUsd': None,
                           'configSha256': config_hash}
                    state.append(row)
                    if not model['catalogAvailable'] or model.get('accessStatus') in (401, 403, 404):
                        row.update(status='unavailable', reason='catalog_or_endpoint_unavailable')
                        write(state_path, state)
                        continue
                    write(state_path, state)  # Durable intent BEFORE sending billable task.
                    pdf = path.read_bytes()
                    if pilot:
                        source = fitz.open(path)
                        one = fitz.open()
                        one.insert_pdf(source, from_page=0, to_page=0)
                        pdf = one.tobytes()
                        one.close()
                        source.close()
                    task = checked(c.post(base + '/tasks', json=task_body(pdf, row['fileName'], model, snapshot, key))).json()['task']
                    row['taskId'] = task['taskId']
                    write(state_path, state)
                elif not row.get('taskId'):
                    row['taskId'] = recover_submission(c, base, row)
                    write(state_path, state)
                started = time.monotonic()
                last_billing_check = started
                last_stage = None
                while True:
                    task = checked(c.get(base + '/tasks/' + row['taskId'])).json()['task']
                    row.update(safe_task(task))
                    metrics = task.get('metrics') or {}
                    row['costUsd'] = ledger_cost(work, row['taskId'], model, metrics)
                    end = task.get('updatedAt') if task['status'] in TERMINAL else now()
                    row['elapsedSeconds'] = (datetime.fromisoformat(end) - datetime.fromisoformat(row['startedAt'])).total_seconds()
                    write(state_path, state)
                    if task.get('stage') != last_stage:
                        print(f"{paper['id']} | {model['name']} | {task['status']} | {task.get('stage')} | ${row['costUsd'] or 0:.5f}", flush=True)
                        last_stage = task.get('stage')
                    if task['status'] in TERMINAL:
                        break
                    tokens = metrics.get('tokens', {})
                    completed_requests = metrics.get('requests', {}).get('succeeded', 0)
                    requests = metrics.get('requests', {})
                    auth_rejections = sum(requests.get('statusCodes', {}).get(str(code), 0) for code in (401, 403))
                    row['rejectedAuthRequests'] = auth_rejections
                    row['costBasis'] = 'token estimate; authentication rejections excluded; billing unverified'
                    unknown = completed_requests > 0 and tokens.get('availability') != 'complete' and auth_rejections != requests.get('failed', 0)
                    if account_bound is not None and time.monotonic()-last_billing_check >= 30:
                        try:
                            account_bound = account_budget_bound(work, snapshot, key)
                        except Exception:
                            checked(c.post(base + '/tasks/' + row['taskId'] + '/cancel'))
                            row['stopReason'] = 'billing_unavailable'
                            write(state_path, state)
                            raise RuntimeError('Billing monitor unavailable; task cancelled, no new spending') from None
                        last_billing_check = time.monotonic()
                    budget_used = max(account_bound or 0, spent + (row['costUsd'] or 0))
                    if budget_used >= snapshot['budgetUsd'] - snapshot['reserveUsd'] or (unknown and account_bound is None) or time.monotonic()-started > 3600:
                        checked(c.post(base + '/tasks/' + row['taskId'] + '/cancel'))
                        row['stopReason'] = 'unknown_usage' if unknown else 'budget_or_timeout'
                    time.sleep(2)
                if task.get('canDownloadResult'):
                    output = checked(c.get(base + '/tasks/' + row['taskId'] + '/result', params={'mode': 'dual'})).content
                    target = work / 'outputs' / (row['taskId'] + '.pdf')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(output)
                    row['outputSha256'] = digest(target)
                write(state_path, state)
                print(f"Finished {paper['id']} / {model['name']}: {row['status']}, cost={row['costUsd']}", flush=True)
                if (row['costUsd'] is None and account_bound is None) or row.get('stopReason'):
                    raise RuntimeError('Execution paused: cost or cancellation requires inspection')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'pilot', 'run'])
    parser.add_argument('--work', type=Path, default=DEFAULT_WORK)
    parser.add_argument('--server', default='http://127.0.0.1:8891')
    parser.add_argument('--paper')
    args = parser.parse_args()
    if args.command == 'prepare':
        prepare(args.work.resolve())
    else:
        run(args.work.resolve(), args.server, args.command == 'pilot', args.paper)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Do not accidentally print SDK exceptions containing request credentials.
        print(f'Benchmark stopped: {type(exc).__name__}: {str(exc) if isinstance(exc, (ValueError, RuntimeError)) else "inspect local state"}', file=sys.stderr)
        sys.exit(1)
