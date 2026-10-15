"""Self-contained reports; missing reviews and incomplete usage stay visible."""
from __future__ import annotations

import csv
import html
import json
import math
import os
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import quote

from .common import inside, digest, read, rows, write


def percentile(values, fraction):
    values = sorted(v for v in values if isinstance(v, (int, float)) and math.isfinite(v))
    return values[max(0, math.ceil(len(values) * fraction) - 1)] if values else None


def selection_request_counts(run, results):
    """Count actual generation calls, never the HTTP jobs or cache lookups."""
    unknown = dict.fromkeys(('attempts', 'succeeded', 'failed', 'cancelled', 'retries', 'repairs'))
    ledger = run / 'runtime' / 'ledger'
    if not ledger.is_dir() or not results or any(
        not isinstance(r.get('call_ids'), list) or r.get('usage', {}).get('calls') != len(r['call_ids'])
        for r in results
    ):
        return unknown
    events = [e for path in sorted(ledger.glob('*.jsonl')) for e in rows(path)]
    starts = {e['callId']: e for e in events if e['event'] == 'request_started'}
    finishes = {e['callId']: e for e in events if e['event'] == 'request_finished'}
    expected = [call for r in results for call in r['call_ids']]
    if len(set(expected)) != len(expected) or set(expected) != starts.keys() or starts.keys() != finishes.keys():
        return unknown
    return {'attempts': len(starts),
            'succeeded': sum(bool(e['succeeded']) for e in finishes.values()),
            'failed': sum(not e['succeeded'] and not e.get('cancelled') for e in finishes.values()),
            'cancelled': sum(bool(e.get('cancelled')) for e in finishes.values()),
            # The ledger does not record scheduled retries. Do not infer them.
            'retries': None if starts else 0,
            'repairs': sum(e.get('retryCause') in {'batch_partial', 'batch_fallback'} for e in starts.values())}


def summarize(run):
    run = Path(run)
    manifest = read(run / 'run.json')
    all_rows = rows(run / 'results.jsonl')
    main = [r for r in all_rows if r['phase'] == 'main']
    samples = read(run / 'samples.json')
    reviews = read(run / 'reviews.json') if (run / 'reviews.json').exists() else {}
    pdf = manifest['identity']['suite'].startswith('pdf-')
    groups, scored, output_scores, items = defaultdict(list), [], [], []
    successful_scores, successful_unreviewed = [], 0
    unreviewed, serious = 0, 0
    for row in main:
        sample, review = samples[row['sample_id']], reviews.get(row['job_id'])
        if review and review.get('result_sha256') != digest(row):
            raise ValueError('Review refers to different output')
        score = review['score'] if review else 0 if not pdf and not row['translation'] and row['status'] == 'error' else None
        unreviewed += score is None
        if row['status'] == 'ok':
            successful_unreviewed += score is None
            if score is not None:
                successful_scores.append(score)
        if score is not None:
            scored.append(score)
            if row['translation'] or row.get('artifacts'):
                output_scores.append(score)
            for field in ('domain', 'origin', 'length', 'split'):
                groups[field + ':' + sample.get(field, 'unknown')].append(score)
        serious += bool(review and any(e['severity'] in {'major', 'critical'} for e in review['errors']))
        items.append({**row, 'source': sample['source_text'], 'domain': sample.get('domain'),
                      'origin': sample.get('origin'), 'length': sample.get('length'), 'split': sample.get('split'),
                      'group': sample.get('group'), 'score': score, 'review': review})
        if sample.get('candidate'):
            items[-1]['candidate'] = sample['candidate']
        if manifest['identity']['suite'] == 'selection':
            items[-1].update(selection_context=sample.get('selection_context', ''),
                             selection_glossary=sample.get('selection_glossary', []))
    latencies = [r.get('elapsed_seconds') for r in main if r['status'] == 'ok']
    ttft = [r.get('ttft_seconds') for r in main if r['status'] == 'ok']
    usage_values = [r.get('usage', {}) for r in all_rows]
    repeats = [r for r in all_rows if r['phase'] == 'repeat']
    initial = {r['sample_id']: r for r in main}
    stability = [{'sample_id': sid, 'runs': 1 + len(values), 'statuses': dict(Counter(v['status'] for v in [initial[sid], *values])),
                  'distinct_outputs': len({v['translation'] for v in [initial[sid], *values]}),
                  'latency_range': [min(v['elapsed_seconds'] for v in [initial[sid], *values] if v.get('elapsed_seconds') is not None),
                                    max(v['elapsed_seconds'] for v in [initial[sid], *values] if v.get('elapsed_seconds') is not None)]}
                 for sid in sorted({r['sample_id'] for r in repeats}) if sid in initial
                 for values in [[r for r in repeats if r['sample_id'] == sid]]
                 if any(v.get('elapsed_seconds') is not None for v in [initial[sid], *values])]
    complete = manifest['status'] == 'completed' and set(r['job_id'] for r in all_rows) == set(manifest['planned_jobs']) and len(all_rows) == len(manifest['planned_jobs'])
    request_metrics = [(r.get('metrics') or {}).get('requests', {}) for r in all_rows]
    request_counts = {
        key: sum(m[key] for m in request_metrics) if request_metrics and all(type(m.get(key)) is int for m in request_metrics) else None
        for key in ('attempts', 'succeeded', 'failed', 'cancelled', 'retries', 'repairs')
    }
    if not pdf and manifest['identity']['suite'] != 'selection':
        request_counts.update(attempts=len(all_rows), succeeded=sum(r['status'] == 'ok' for r in all_rows),
                              failed=sum(r['status'] == 'error' for r in all_rows), retries=0, repairs=0)
    elif manifest['identity']['suite'] == 'selection' and request_counts['attempts'] is None:
        request_counts = selection_request_counts(run, all_rows)
    return {'run_id': manifest['run_id'], 'suite': manifest['identity']['suite'], 'model': manifest['identity']['provider']['model'],
            'configuration': manifest['identity'],
            'evaluation_scope': manifest['identity'].get('evaluation_scope', 'frozen_dataset'),
            'resumed': manifest.get('resumed', False),
            'dataset_sha256': manifest['identity']['dataset_sha256'], 'complete': complete,
            'main_samples': len(main), 'planned_jobs': len(manifest['planned_jobs']), 'executed_jobs': len(all_rows),
            'statuses': dict(Counter(r['status'] for r in main)), 'success_rate': sum(r['status'] == 'ok' for r in main) / len(main) if main else None,
            'quality_score': statistics.mean(scored) if scored and not unreviewed and complete else None,
            'reviewed_subset_score': statistics.mean(scored) if scored else None,
            'output_quality_score': statistics.mean(successful_scores) if successful_scores and not successful_unreviewed else None,
            'reviewed_outputs': len(output_scores), 'unreviewed': unreviewed,
            'serious_error_rate': serious / len(output_scores) if output_scores else None,
            'unknown_outcomes': sum(r['status'] == 'unknown' for r in all_rows),
            'latency_p50': percentile(latencies, .5), 'latency_p95': percentile(latencies, .95),
            'ttft_p50': percentile(ttft, .5), 'ttft_p95': percentile(ttft, .95),
            'first_paragraph_p50': percentile([r.get('first_paragraph_seconds') for r in main], .5),
            'first_paragraph_p95': percentile([r.get('first_paragraph_seconds') for r in main], .95),
            'tokens': {k: sum(u[k] for u in usage_values if type(u.get(k)) is int) if any(type(u.get(k)) is int for u in usage_values) else None for k in ('input', 'output', 'cached_input', 'reasoning')},
            'usage_complete': bool(usage_values) and all(u.get('availability') == 'complete' for u in usage_values),
            'strata_reviewed_scores': {k: {'score': statistics.mean(v), 'count': len(v)} for k, v in sorted(groups.items())},
            'correlation_groups': len({item['group'] for item in items}),
            'repeat_stability': stability, 'stability_note': 'Literal output variation is not a semantic quality score',
            'total_elapsed_seconds': manifest.get('elapsed_seconds'),
            'cache_hits': dict(Counter(r['cache_source'] for r in main if r.get('cache_source'))),
            'pdf_quality': {field: statistics.mean(r[field] for r in reviews.values() if field in r)
                            if any(field in r for r in reviews.values()) else None
                            for field in ('content_completeness', 'layout_readability')} if pdf else None,
            'execution_metrics': [r['metrics'] for r in main if r.get('metrics')],
            'request_counts': request_counts,
            'independent_review': bool(reviews) and all(r['independent'] for r in reviews.values()), 'items': items}


def report(runs, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    summaries = [summarize(run) for run in runs]
    for run, summary in zip(runs, summaries):
        for item in summary['items']:
            for artifact in item.get('artifacts', []):
                artifact['href'] = quote(os.path.relpath(inside(Path(run), artifact['path']), output))
    write(output / 'report.json', summaries)
    fields = ['run_id', 'model', 'sample_id', 'domain', 'origin', 'length', 'split', 'status', 'score', 'elapsed_seconds', 'ttft_seconds', 'translation']
    with (output / 'results.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fields)
        writer.writeheader()
        for summary in summaries:
            for row in summary['items']:
                value = {k: row.get(k) for k in fields}
                value.update(run_id=summary['run_id'], model=summary['model'])
                # Spreadsheet exports must not execute text produced by a model.
                value = {k: "'" + v if isinstance(v, str) and v.startswith(('=', '+', '-', '@')) else v for k, v in value.items()}
                writer.writerow(value)
    data = json.dumps(summaries, ensure_ascii=False).replace('<', '\\u003c').replace('&', '\\u0026')
    template = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>翻译测评报告</title>
<style>body{font:16px/1.6 system-ui,sans-serif;background:#f4f6f8;color:#172b3a;margin:0;padding:32px}main{max-width:1280px;margin:auto}h1{margin-bottom:8px}.cards{display:flex;gap:16px;flex-wrap:wrap}.card,details{background:white;border:1px solid #d6e0e8;border-radius:12px;padding:20px;margin:12px 0}.card{flex:1;min-width:260px}select,input{font:inherit;padding:7px;margin-right:12px;max-width:100%}.pair{display:grid;grid-template-columns:1fr 1fr;gap:24px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}.muted{color:#596c7a}.bad{color:#ad3030}summary{cursor:pointer}table{border-collapse:collapse}td,th{padding:8px;text-align:left;border-bottom:1px solid #ddd}@media(max-width:720px){body{padding:16px}.pair{grid-template-columns:1fr}}</style>
<main><h1>英译中测评报告</h1><p class="muted">离线报告 · 未评审显示“未评审” · 不同数据版本、模型任务与 PDF 流程分开比较</p><div id="cards" class="cards"></div><p><select id="model"><option value="">全部模型</option></select><select id="domain"><option value="">全部领域</option></select><select id="status"><option value="">全部状态</option><option>ok</option><option>error</option><option>unknown</option></select><input id="search" placeholder="搜索样本或译文"></p><div id="count"></div><div id="cases"></div></main>
<script type="application/json" id="data">__DATA__</script><script>
const runs=JSON.parse(document.getElementById('data').textContent);const all=[];
function element(tag,text,parent,cls){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;if(parent)parent.append(e);return e}
function metric(v,suffix=''){return v===null||v===undefined?'未评审 / 不可用':Number(v).toFixed(2)+suffix}
for(const run of runs){const c=element('section',undefined,document.getElementById('cards'),'card');element('h2',run.model,c);element('p',run.suite+' · '+(run.evaluation_scope==='candidate_diagnostics'?'候选诊断（不计入冻结基线） · ':'')+(run.complete?'运行完整':'运行未完成'),c);element('p','成功率 '+metric(run.success_rate===null?null:run.success_rate*100,'%')+' · 译文评分 '+metric(run.quality_score),c);element('p','P50 '+metric(run.latency_p50,'s')+' / P95 '+metric(run.latency_p95,'s'),c);element('p','未评审 '+run.unreviewed+' · 用量'+(run.usage_complete?'完整':'不完整')+' · '+(run.independent_review?'独立评审':'未声明独立评审'),c,'muted');if(run.pdf_quality){element('p','PDF 内容 '+metric(run.pdf_quality.content_completeness,' / 4')+' · 排版 '+metric(run.pdf_quality.layout_readability,' / 4'),c);element('p','首段 P50 '+metric(run.first_paragraph_p50,'s'),c)}element('p','总耗时 '+metric(run.total_elapsed_seconds,'s')+' · 调用尝试 '+metric(run.request_counts.attempts),c);element('p','重试 '+metric(run.request_counts.retries)+' · 批次修复 '+metric(run.request_counts.repairs),c);element('p','Token 输入 '+metric(run.tokens.input)+' / 输出 '+metric(run.tokens.output),c);element('p','其中缓存输入 '+metric(run.tokens.cached_input)+' · 推理输出 '+metric(run.tokens.reasoning),c,'muted');const config=element('details',undefined,c);element('summary','运行配置与可比性',config);element('pre',JSON.stringify(run.configuration,null,2),config);element('small',run.run_id,c);for(const item of run.items)all.push({...item,model:run.model,run_id:run.run_id})}
for(const key of ['model','domain']){const select=document.getElementById(key);for(const value of [...new Set(all.map(x=>x[key]).filter(Boolean))].sort()){const o=element('option',value,select);o.value=value}}
function render(){const filters=['model','domain','status'];const query=document.getElementById('search').value.toLowerCase();const values=all.filter(x=>filters.every(k=>!document.getElementById(k).value||x[k]===document.getElementById(k).value)&&(!query||[x.sample_id,x.source,x.translation].join(' ').toLowerCase().includes(query)));document.getElementById('cases').replaceChildren();document.getElementById('count').textContent=values.length+' 条结果';for(const row of values){const d=element('details',undefined,document.getElementById('cases'));element('summary',row.model+' · '+row.sample_id+' · '+row.status+' · '+metric(row.score),d);const pair=element('div',undefined,d,'pair');element('pre',row.source,pair);element('pre',row.translation||(row.artifacts?.length?'PDF 产物（见下方链接）':'（无输出）'),pair);for(const artifact of row.artifacts||[]){const a=element('a',artifact.mode+' PDF',d);a.href=artifact.href;element('span',' ',d)}element('p',row.domain+' / '+row.length+' / '+row.split,d,'muted');element('pre',JSON.stringify({rules:row.rules,usage:row.usage,review:row.review,error:row.error,selection_context:row.selection_context,selection_glossary:row.selection_glossary,candidate:row.candidate,artifacts:row.artifacts,metrics:row.metrics},null,2),d)}}
for(const id of ['model','domain','status','search'])document.getElementById(id).addEventListener('input',render);render();</script></html>'''
    (output / 'index.html').write_text(template.replace('__DATA__', data), encoding='utf-8')
    return str(output / 'index.html')


def dataset_report(data, output):
    """Browse the actual corpus without inventing model outputs or quality scores."""
    from .corpus import verify
    from .common import DOMAINS
    data, output = Path(data), Path(output)
    verified = verify(data)
    samples, sources = rows(data / 'samples.jsonl'), read(data / 'sources.json')
    output.mkdir(parents=True, exist_ok=True)
    write(output / 'coverage.json', {**verified, 'domains': dict(Counter(s['domain'] for s in samples)),
                                   'lengths': dict(Counter(s['length'] for s in samples)), 'sources': sources})
    fields = ['id', 'domain', 'origin', 'split', 'length', 'word_count', 'group', 'source_sha256']
    with (output / 'samples.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(samples)
    esc = html.escape
    body = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>CS 英译中基线 v2</title>',
            '<style>body{font:16px/1.65 system-ui;margin:0;background:#f4f6f8;color:#173346}main{max-width:1100px;margin:auto;padding:32px}section,details{background:white;border:1px solid #d6e0e8;border-radius:12px;padding:18px;margin:14px 0}h1{font-size:32px}h2{font-size:22px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}.meta{color:#586e7a}summary{cursor:pointer}a{color:#146ca2}input,select{font:inherit;padding:9px;max-width:100%}table{border-collapse:collapse;width:100%}td,th{text-align:left;border-bottom:1px solid #ddd;padding:9px}</style>',
            '<main><h1>计算机领域 · 英译中测评基线 v2</h1><p class="meta">数据集浏览与验收报告 · 尚未运行真实模型 · 尚无新基准分数</p>',
            '<section><h2>16 篇论文 · 240 条样本 · 48 个页面锚点</h2><p>160 条真实片段＋80 条专项样本；开发集与保留验收集各 120 条。</p><p>长度分布：短句 48 / 常规段落 96 / 长段落 72 / 长文本 24。</p><p>论文正文仅随本地数据包保存。标注包含来源证据与可接受术语，尚未声称经过独立专家评审。</p><p class="meta">内容指纹 '+esc(verified['content_sha256'])+'</p></section>',
            '<section><h2>来源与覆盖</h2><table><tr><th>领域</th><th>开发集论文</th><th>保留集论文</th></tr>']
    for domain in DOMAINS:
        pair = sorted((s for s in sources if s['domain'] == domain), key=lambda s:s['split'])
        body.append('<tr><td>'+esc(domain)+'</td>'+''.join('<td><a href="'+esc(s['url'], quote=True)+'">'+esc(s['title'])+'</a><br><small>'+esc(s['version'])+' · '+str(s['pages'])+' 页</small></td>' for s in pair)+'</tr>')
    body.extend(['</table></section><h2>样本浏览</h2><p><select id="split"><option value="development">开发集</option><option value="holdout">保留验收集（正式验收使用）</option></select> <input id="filter" placeholder="搜索编号、领域或标签"></p><p id="count" class="meta"></p>'])
    for sample in samples:
        key = ' '.join([sample['id'], sample['domain'], *sample['tags']])
        body.append('<details class="sample" data-split="'+sample['split']+'" data-key="'+esc(key, quote=True)+'"><summary>'+esc(sample['id'])+' · '+esc(sample['domain'])+' · '+str(sample['word_count'])+' 词 · '+esc(sample['origin'])+'</summary><pre>'+esc(sample['source_text'])+'</pre><p class="meta">'+esc(', '.join(sample['tags']))+'</p><pre>'+esc(json.dumps({k:sample.get(k) for k in ('source_locations','key_points','acceptable_terms','must_preserve','group')}, ensure_ascii=False, indent=2))+'</pre></details>')
    body.append('</main><script>function filter(){let count=0;for(const item of document.querySelectorAll(".sample")){item.hidden=item.dataset.split!==document.getElementById("split").value||!item.dataset.key.toLowerCase().includes(document.getElementById("filter").value.toLowerCase());if(!item.hidden)count++}document.getElementById("count").textContent=count+" 条样本"}document.getElementById("split").onchange=filter;document.getElementById("filter").oninput=filter;filter()</script></html>')
    (output / 'index.html').write_text('\n'.join(body), encoding='utf-8')
    return {'report': str(output / 'index.html'), **verified}


def stability(runs):
    """Repeated observations of one frozen configuration, not an improvement claim."""
    paths = [Path(p).resolve() for p in runs]
    manifests = [read(p / 'run.json') for p in paths]
    if len(paths) < 3 or len(set(paths)) != len(paths) or len({m['run_id'] for m in manifests}) != len(paths):
        raise ValueError('Stability requires at least three distinct real runs')
    if any(m['fingerprint'] != digest(m['identity']) for m in manifests):
        raise ValueError('Run identity fingerprint mismatch')
    if len({m['fingerprint'] for m in manifests}) != 1:
        raise ValueError('Stability requires identical code, data, prompt, model, cache and environment')
    summaries = [summarize(p) for p in paths]
    if any(not s['complete'] or s['resumed'] for s in summaries):
        raise ValueError('Stability requires complete uninterrupted runs; keep failed samples visible')
    metrics = {}
    for field in ('total_elapsed_seconds', 'latency_p50', 'latency_p95', 'ttft_p50', 'ttft_p95'):
        values = [s[field] for s in summaries]
        if any(v is None for v in values):
            metrics[field] = {'values': values, 'complete': False}
            continue
        mean, deviation = statistics.mean(values), statistics.stdev(values)
        metrics[field] = {'values': values, 'complete': True, 'mean': mean,
                          'min': min(values), 'max': max(values), 'sample_stdev': deviation,
                          'coefficient_of_variation': deviation / mean if mean else None}
    return {'schema': 1, 'kind': 'same_configuration_observations', 'runs': len(paths),
            'fingerprint': manifests[0]['fingerprint'], 'metrics': metrics,
            'performance_claim_allowed': False, 'baseline_promoted': False,
            'note': 'Application cold cache does not disable provider prompt caching. Latency percentiles use successful outputs; statuses include failures.',
            'observations': [{k: s[k] for k in ('run_id', 'statuses', 'success_rate', 'request_counts',
                                              'tokens', 'usage_complete', 'cache_hits')} for s in summaries]}


def compare(left, right):
    left, right = list(map(Path, left)), list(map(Path, right))
    manifests = [read(p / 'run.json') for p in left + right]
    if not manifests:
        raise ValueError('Runs are required')
    identities = [m['identity'] for m in manifests]
    shared = ('dataset_sha256', 'suite', 'prompt_version')
    compatible = all(all(i[k] == identities[0][k] for k in shared)
                     and i.get('candidates') == identities[0].get('candidates') for i in identities)
    summaries = [summarize(p) for p in left + right]
    conditions = ('environment', 'cache', 'concurrency')
    same_runtime = all(all(i[k] == identities[0][k] for k in conditions) for i in identities)
    def model_key(identity):
        return {k: v for k, v in identity['provider'].items() if k not in {'limits', 'pair_id', 'prices_per_million', 'client_sha256'}}
    same_model = all(model_key(i) == model_key(identities[0]) for i in identities)
    pairs_a = [i['provider'].get('pair_id') for i in identities[:len(left)]]
    pairs_b = [i['provider'].get('pair_id') for i in identities[len(left):]]
    paired = len(left) == len(right) >= 3 and None not in pairs_a + pairs_b and len(set(pairs_a)) == len(left) and set(pairs_a) == set(pairs_b)
    performance = compatible and same_runtime and same_model and paired and all(s['complete'] and s['success_rate'] == 1 and not s['resumed'] for s in summaries)
    deltas = []
    if performance:
        before = {pair: summary for pair, summary in zip(pairs_a, summaries[:len(left)])}
        for pair, summary in zip(pairs_b, summaries[len(left):]):
            deltas.append(summary['latency_p50'] - before[pair]['latency_p50'])
    return {'quality_comparable': compatible, 'performance_claim_allowed': performance,
            'reason': 'At least three complete paired runs with the same model, environment, cache and concurrency are required',
            'paired_latency_p50_deltas': deltas, 'mean_delta': statistics.mean(deltas) if deltas else None,
            'delta_range': [min(deltas), max(deltas)] if deltas else None,
            'runs': [{k: v for k, v in s.items() if k != 'items'} for s in summaries]}
