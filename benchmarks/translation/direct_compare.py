"""P0 paired body-batch replay: no product settings or baseline mutations."""
from __future__ import annotations

import ast
import csv
import html
import json
from pathlib import Path
import statistics
from string import Template
import sys
import time
import uuid

from .chatgpt_auth import AuthStore, DEFAULT_AUTH, login
from .chatgpt_direct import DirectProvider
from .common import ROOT, append, code_identity, digest, environment, file_hash, now, read, rows, write, INSTRUCTION
from .providers import CodexProvider
from .runner import SMOKE

BODY_IDS = ('t5', 'nerf', 'raft', 'duckdb', 'rustbelt', 'dp', 'vegalite', 'kll')


def product_template():
    path = ROOT / 'server/babeldoc/format/pdf/document_il/midend/il_translator_llm_only.py'
    tree = ast.parse(path.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(n, ast.Name) and n.id == 'PROMPT_TEMPLATE' for n in node.targets):
            return Template(ast.literal_eval(node.value.args[0])), file_hash(path)
    raise ValueError('Product prompt template not found')


def prepare(data, output, *, model, effort, cli_path, rounds=3, max_requests=60, max_seconds=1800):
    output = Path(output)
    if output.exists():
        raise ValueError('Choose a new output directory; never overwrite a P0 experiment')
    if rounds not in range(1, 4) or not 0 < max_seconds <= 3600:
        raise ValueError('P0 permits 1–3 rounds and at most one hour')
    samples = {x['id']: x for x in rows(Path(data) / 'samples.jsonl')}
    template, template_hash = product_template()
    cases = []
    for paper in BODY_IDS:
        parts = [samples[f'{paper}-{index:02d}'] for index in (3, 7)]
        if any(x['origin'] != 'real' or x['split'] != 'development' for x in parts):
            raise ValueError('Only real development-set passages are allowed')
        inputs = [{'id': i, 'input': part['source_text'], 'layout_label': 'text'} for i, part in enumerate(parts)]
        prompt = template.substitute(
            role_block='You are a professional zh-CN native translator who needs to fluently translate text into zh-CN.\n\nFollow all rules strictly.',
            glossary_usage_rules_block='', contextual_hints_block='',
            json_input_str=json.dumps(inputs, ensure_ascii=False, indent=2), glossary_tables_block='', lang_out='zh-CN')
        cases.append({'id': paper, 'domain': parts[0]['domain'], 'parts': parts,
                      'prompt': prompt, 'prompt_sha256': digest(prompt.encode()),
                      'source_words': sum(len(x['source_text'].split()) for x in parts)})
    prechecks = [{'id': x['id'], 'prompt': INSTRUCTION + '\n\n' + x['source_text']} for x in SMOKE]
    schedule = []
    for case in prechecks:
        for route in ('direct', 'codex'):
            schedule.append({'id': 'precheck:' + case['id'] + ':' + route, 'phase': 'precheck',
                             'case': case['id'], 'round': 0, 'route': route})
    for repeat in range(1, rounds + 1):
        for index, case in enumerate(cases):
            order = ('codex', 'direct') if (repeat + index) % 2 else ('direct', 'codex')
            for route in order:
                schedule.append({'id': f'body:{repeat}:{case["id"]}:{route}', 'phase': 'body',
                                 'case': case['id'], 'round': repeat, 'route': route})
    if not len(schedule) <= max_requests <= 100:
        raise ValueError('max_requests must cover the schedule and be at most 100')
    sys.path.insert(0, str(ROOT / 'server'))
    from codex_client import INSTRUCTIONS
    manifest = {'schema': 1, 'kind': 'p0_body_batch_replay', 'run_id': uuid.uuid4().hex,
        'created_at': now(), 'status': 'prepared', 'code': code_identity(), 'environment': environment(),
        'data_sha256': file_hash(Path(data) / 'samples.jsonl'), 'template_sha256': template_hash,
        'model': model, 'reasoning_effort': effort, 'cli_path': cli_path, 'rounds': rounds,
        'limits': {'max_requests': max_requests, 'max_seconds': max_seconds, 'timeout_seconds': 120},
        'instructions': INSTRUCTIONS, 'instructions_sha256': digest(INSTRUCTIONS.encode()),
        'cases': cases, 'prechecks': prechecks, 'schedule': schedule,
        'application_cache': 'disabled', 'concurrency': 1, 'automatic_retries': 0,
        'baseline_promoted': False, 'product_default_changed': False,
        'scope_note': 'Replays the real PDF body-batch prompt using frozen development passages. Excludes PDF extraction/layout and is not an end-to-end PDF benchmark.',
        'uncontrolled': ['provider_prompt_cache', 'provider_internal_retries', 'server_model_routing'],
        'workspace_match': 'operator_selected_not_machine_verified'}
    manifest['input_fingerprint'] = digest({k: manifest[k] for k in ('cases', 'prechecks', 'schedule', 'model', 'reasoning_effort', 'instructions_sha256')})
    write(output / 'experiment.json', manifest)
    make_report(output)
    return {'prepared': True, 'planned_requests': len(schedule), 'body_batches': len(cases), 'rounds': rounds,
            'output': str(output), 'requires_independent_chatgpt_login': True}


def inspect(case, text):
    sys.path.insert(0, str(ROOT / 'server'))
    from babeldoc.translator.validation import inspect_batch, InvalidTranslation
    try:
        result = inspect_batch(text, [p['source_text'] for p in case['parts']], 'zh-CN')
        return {'complete': result.complete, 'valid_paragraphs': len(result.valid_outputs),
                'errors': result.invalid_outputs, 'unassigned_rows': result.unassigned_rows,
                'outputs': result.valid_outputs, 'semantic_review': 'unreviewed'}
    except InvalidTranslation as error:
        return {'complete': False, 'valid_paragraphs': 0, 'errors': {'batch': str(error)}, 'semantic_review': 'unreviewed'}


def run(output, auth_directory=DEFAULT_AUTH, *, execute=False):
    output = Path(output)
    manifest = read(output / 'experiment.json')
    if not execute:
        return {'planned_requests': len(manifest['schedule']), 'limits': manifest['limits'], 'executes_model': False}
    if manifest['code'] != code_identity():
        raise ValueError('Code changed after preparation. Prepare a new experiment; do not mix configurations.')
    if rows(output / 'calls.jsonl') or rows(output / 'events.jsonl'):
        raise ValueError('This experiment already has calls; prepare a new run to avoid ambiguous replay')
    store = AuthStore(auth_directory)
    if not store.status().get('plan_usage_authorized'):
        raise ValueError('Independent ChatGPT plan authorization is required before comparison')
    direct = DirectProvider(model=manifest['model'], reasoning_effort=manifest['reasoning_effort'],
                            auth_directory=auth_directory, store=store, instructions=manifest['instructions'])
    codex = None
    started = time.monotonic()
    manifest.update(status='running', started_at=now(), auth=store.status())
    write(output / 'experiment.json', manifest)
    try:
        catalog = direct.list_models()
        write(output / 'direct-models.json', catalog)
        if not catalog.get('ok') or manifest['model'] not in {m['id'] for m in catalog['models']}:
            raise ValueError('Requested model is unavailable in direct catalog; no model substitution performed')
        codex_started = time.monotonic()
        codex = CodexProvider({'model': manifest['model'], 'reasoning_effort': manifest['reasoning_effort'],
                               'cli_path': manifest['cli_path'], 'proxy_mode': 'inherit'})
        manifest['codex_initialization_seconds'] = time.monotonic() - codex_started
        manifest['codex_actual'] = codex.actual
        # Compare identity without persisting email or the raw account object.
        account = codex.client._ensure_started().call('account/read', {'refreshToken': False}).get('account') or {}
        own_email = store.read().get('email')
        manifest['account_email_match'] = (own_email.casefold() == account['email'].casefold()
            if isinstance(own_email, str) and isinstance(account.get('email'), str) else None)
        if manifest['account_email_match'] is False:
            raise ValueError('Direct and Codex accounts differ; use the same account before comparison')
        cases = {x['id']: x for x in manifest['cases'] + manifest['prechecks']}
        for index, job in enumerate(manifest['schedule']):
            if index >= manifest['limits']['max_requests'] or time.monotonic() - started >= manifest['limits']['max_seconds']:
                raise ValueError('P0 execution budget exhausted')
            call_id = uuid.uuid4().hex
            append(output / 'events.jsonl', {'event': 'started', 'call_id': call_id, 'job': job, 'at': now()})
            provider = direct if job['route'] == 'direct' else codex
            remaining = manifest['limits']['max_seconds'] - (time.monotonic() - started)
            result = provider.generate(cases[job['case']]['prompt'], min(manifest['limits']['timeout_seconds'], remaining))
            row = {**job, **result, 'call_id': call_id, 'completed_at': now(),
                   'prompt_sha256': digest(cases[job['case']]['prompt'].encode())}
            if job['phase'] == 'body':
                row['validation'] = inspect(cases[job['case']], row['translation'])
            append(output / 'calls.jsonl', row)
            append(output / 'events.jsonl', {'event': 'finished', 'call_id': call_id, 'at': now(), 'status': row['status']})
            print(json.dumps({'job': job['id'], 'status': row['status'], 'seconds': round(row['elapsed_seconds'], 2),
                              'input_tokens': row['usage']['input'], 'output_tokens': row['usage']['output']}), flush=True)
            # This minimal evaluation never hides first failures behind retries.
            if row['status'] != 'ok':
                manifest['stop_reason'] = {'job': job['id'], 'error': row['error']}
                break
        count = len(rows(output / 'calls.jsonl'))
        manifest['status'] = 'completed' if count == len(manifest['schedule']) and not manifest.get('stop_reason') else 'incomplete'
    except (Exception, KeyboardInterrupt) as error:
        manifest['status'] = 'incomplete'
        # Internal ValueError messages are controlled; external errors are type-only.
        manifest['stop_reason'] = {'type': type(error).__name__,
            'message': str(error) if type(error) is ValueError else None}
    finally:
        if codex:
            codex.close()
        direct.close()
        manifest['elapsed_seconds'] = time.monotonic() - started
        manifest['finished_at'] = now()
        write(output / 'experiment.json', manifest)
        make_report(output)
    return {'status': manifest['status'], 'report': str(output / 'report.html'), 'baseline_promoted': False}


def make_report(output):
    output = Path(output)
    manifest, calls = read(output / 'experiment.json'), rows(output / 'calls.jsonl')
    summary = {'status': manifest['status'], 'scope': manifest['scope_note'], 'planned_requests': len(manifest['schedule']),
               'recorded_calls': len(calls), 'baseline_promoted': False, 'product_default_changed': False,
               'semantic_review': 'unreviewed', 'routes': {}, 'paired_rounds': []}
    for route in ('codex', 'direct'):
        group = [r for r in calls if r['route'] == route and r['phase'] == 'body']
        good = [r for r in group if r['status'] == 'ok']
        expected = len(manifest['cases']) * manifest['rounds']
        totals = {key: sum(r['usage'][key] for r in group if type(r['usage'].get(key)) is int)
                  for key in ('input', 'output', 'cached_input', 'reasoning')}
        known = {key: sum(type(r['usage'].get(key)) is int for r in group) for key in totals}
        summary['routes'][route] = {'calls': len(group), 'expected_calls': expected, 'succeeded': len(good),
            'complete': len(group) == expected, 'known_token_subtotals': totals, 'known_token_calls': known,
            'elapsed_seconds': sum(r['elapsed_seconds'] for r in group) if group else None,
            'latency_p50': statistics.median(r['elapsed_seconds'] for r in good) if good else None,
            'structurally_valid': sum(r.get('validation', {}).get('complete', False) for r in group),
            'usage_complete': bool(group) and all(r['usage']['availability'] == 'complete' for r in group)}
    for repeat in range(1, manifest['rounds'] + 1):
        values = {}
        for route in ('codex', 'direct'):
            group = [r for r in calls if r['phase'] == 'body' and r['round'] == repeat and r['route'] == route]
            if len(group) == len(manifest['cases']) and all(r['status'] == 'ok' for r in group):
                values[route] = sum(r['elapsed_seconds'] for r in group)
        if len(values) == 2:
            summary['paired_rounds'].append({'round': repeat, 'seconds': values,
                                            'elapsed_reduction': 1 - values['direct'] / values['codex']})
    summary['performance_claim_allowed'] = False  # Requires quality review and an explicit human decision.
    write(output / 'summary.json', summary)
    with (output / 'calls.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(['job', 'route', 'phase', 'round', 'status', 'seconds', 'ttft', 'input', 'output', 'cached_input', 'usage_availability'])
        for row in calls:
            writer.writerow([row['id'], row['route'], row['phase'], row['round'], row['status'], row['elapsed_seconds'],
                             row.get('ttft_seconds'), row['usage']['input'], row['usage']['output'],
                             row['usage']['cached_input'], row['usage']['availability']])
    esc = lambda value: html.escape(str(value))
    parts = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>ChatGPT 独立授权 P0 对照</title>',
        '<style>body{max-width:1150px;margin:40px auto;padding:0 24px;font:16px/1.7 system-ui;color:#172534;background:#f6f8fa}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:white;padding:18px;border:1px solid #dce2e7}details{margin:16px 0}summary{cursor:pointer;font-weight:600}</style>',
        '<h1>ChatGPT 独立授权 · 正文批次 P0 对照</h1>',
        '<p>保持现有入口与默认设置；未晋升基准。此实验不包含 PDF 提取与排版，不能作为整篇 PDF 提速结论。</p>',
        '<p>语义质量：未评审。结构校验通过不代表语义正确。未知 Token 不等于零；缓存输入属于输入 Token 的子集。</p>',
        '<pre>' + esc(json.dumps(summary, ensure_ascii=False, indent=2)) + '</pre>']
    cases = {x['id']: x for x in manifest['cases']}
    for row in calls:
        parts.append('<details><summary>' + esc(row['id']) + ' · ' + esc(row['status']) + ' · ' + esc(round(row['elapsed_seconds'], 2)) + ' 秒</summary>')
        if row['case'] in cases:
            parts.append('<h3>正文原文</h3><pre>' + esc('\n\n'.join(p['source_text'] for p in cases[row['case']]['parts'])) + '</pre>')
        parts.append('<h3>输出</h3><pre>' + esc(row['translation']) + '</pre><pre>' +
                     esc(json.dumps({k: row.get(k) for k in ('usage', 'error', 'validation', 'actual_configuration')}, ensure_ascii=False, indent=2)) + '</pre></details>')
    parts.append('</html>')
    (output / 'report.html').write_text('\n'.join(parts), encoding='utf-8')
    return summary
