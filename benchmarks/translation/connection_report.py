"""Offline, descriptive report for the bounded connection comparison experiment."""
import argparse
from collections import Counter
import csv
from html import escape
import math
from pathlib import Path

from .common import file_hash, read, rows, write


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)] if values else None


def known_sum(values):
    return sum(values) if all(value is not None for value in values) else None


def summarize(directory):
    directory = Path(directory)
    manifest = read(directory / 'comparison.json')
    runs, payloads, issues = [], {}, []
    for item in manifest['runs']:
        path = directory / item['directory']
        results = rows(path / 'results.jsonl')
        events = [e for p in (path / 'runtime/ledger').glob('*.jsonl') for e in rows(p)]
        started = {e['callId'] for e in events if e['event'] == 'request_started'}
        finished = {e['callId']: e for e in events if e['event'] == 'request_finished'}
        trace = [e for p in (path / 'runtime/connection-trace').glob('*.jsonl') for e in rows(p)]
        posts = [e for e in trace if e['method'] == 'POST']
        payloads[item['directory']] = Counter(e['body_sha256'] for e in posts)
        stages = [s for e in trace for s in e['stages']]
        if len(results) != 4 or len({r['sample_id'] for r in results}) != 4:
            issues.append(f"{item['directory']}: incomplete or duplicate pages")
        if any(r['status'] != 'ok' for r in results):
            issues.append(f"{item['directory']}: failed page result")
        if started != set(finished) or len(started) != item['calls'] or len(posts) != item['calls']:
            issues.append(f"{item['directory']}: call ledger / HTTP trace mismatch")
        if sum(r['usage']['calls'] for r in results) != len(started):
            issues.append(f"{item['directory']}: page usage call count mismatch")
        if read(path / 'run.json')['status'] != 'completed':
            issues.append(f"{item['directory']}: run not completed")
        latency = [e['latencyMs'] / 1000 for e in finished.values() if e.get('latencyMs') is not None]
        entry = {**item, 'seconds': sum(r['elapsed_seconds'] for r in results),
                 'first_paragraph_p50_s': percentile([r['first_paragraph_seconds'] for r in results
                                                     if r.get('first_paragraph_seconds') is not None], .5),
                 'page_p50_s': percentile([r['elapsed_seconds'] for r in results], .5),
                 'page_p95_s': percentile([r['elapsed_seconds'] for r in results], .95),
                 'request_p50_s': percentile(latency, .5), 'request_p95_s': percentile(latency, .95),
                 'input_tokens': known_sum([r['usage']['input'] for r in results]),
                 'output_tokens': known_sum([r['usage']['output'] for r in results]),
                 'cached_input_tokens': known_sum([r['usage']['cached_input'] for r in results]),
                 'usage_complete': all(r['usage']['availability'] == 'complete' for r in results),
                 'failed_calls': sum(not e['succeeded'] and not e['cancelled'] for e in finished.values()),
                 'cancelled_calls': sum(e['cancelled'] for e in finished.values()),
                 'http_requests': len(trace),
                 'connections': sum(s['name'] == 'connection.connect_tcp' for s in stages),
                 'connect_tls_seconds': sum(s['seconds'] for s in stages
                                            if s['name'] == 'connection.connect_tcp' or s['name'].endswith('start_tls')),
                 'http_failures': sum(e.get('status', 500) >= 400 for e in trace),
                 'retries': sum(r['metrics']['requests']['retries'] for r in results),
                 'repairs': sum(r['metrics']['requests']['repairs'] for r in results),
                 'cache_hits': sum(r['metrics']['localCache']['hits'] for r in results),
                 'cache_misses': sum(r['metrics']['localCache']['misses'] for r in results),
                 'paragraph_failed': sum(r['translation_summary']['failed'] for r in results),
                 'paragraph_pending': sum(r['translation_summary']['pending'] for r in results)}
        runs.append(entry)
    indexed = {(r['round'], r['cache'], r['variant']): r for r in runs}
    expected = {(i,c,v) for i in range(1,4) for c in ('cold','warm') for v in ('before','after')}
    if set(indexed) != expected or len(runs) != len(expected) or manifest['status'] != 'completed':
        issues.append('Three complete cold/warm pairs are required')
    for variant in ('before', 'after'):
        if file_hash(directory / f'{variant}_client.py') != manifest[variant]['sha256']:
            issues.append(f'{variant}: frozen client hash mismatch')
    pairs = []
    for i in range(1,4):
        for cache in ('cold','warm'):
            before, after = indexed.get((i,cache,'before')), indexed.get((i,cache,'after'))
            if not before or not after:
                continue
            same_payload = payloads[before['directory']] == payloads[after['directory']]
            if not same_payload:
                issues.append(f'round-{i}-{cache}: request payloads differ')
            pairs.append({'round':i,'cache':cache,'before_seconds':before['seconds'],
                          'after_seconds':after['seconds'],
                          'reduction_percent':100*(before['seconds']-after['seconds'])/before['seconds']
                          if before['seconds'] else None, 'same_payloads':same_payload})
    return {'status':manifest['status'], 'model':manifest['config']['model'],
            'reasoning_effort':manifest['config'].get('reasoning_effort'),
            'client_sha256':{v:manifest[v]['sha256'] for v in ('before','after')},
            'baseline_promoted':False, 'formal_quality_review':'not_completed',
            'integrity_issues':issues, 'runs':runs, 'pairs':pairs,
            'notes':['Descriptive measurements; the report never decides deployment or promotes a baseline.',
                     'Cold and warm results are separate. Both warm variants use the same before-cold seed.',
                     'Connect/TLS durations include overlapping work and are not wall-clock savings.',
                     'P50/P95 use nearest rank. No requests means request latency is unknown, not zero.',
                     'Frozen client hashes identify the loaded implementation; working-tree hashes may differ.']}


def report(directory, output):
    result = summarize(directory)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    write(output / 'comparison-summary.json', result)
    with (output / 'comparison-runs.csv').open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=list(result['runs'][0]) if result['runs'] else ['directory'])
        writer.writeheader(); writer.writerows(result['runs'])
    def table(items, fields):
        def cell(value):
            return escape('—' if value is None else f'{value:.2f}' if isinstance(value,float) else str(value))
        return '<table><tr>' + ''.join(f'<th>{escape(f)}</th>' for f in fields) + '</tr>' + ''.join(
            '<tr>' + ''.join(f'<td>{cell(item.get(f))}</td>' for f in fields) + '</tr>' for item in items) + '</table>'
    html = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>ChatGPT 连接复用对照</title>'
            '<style>body{font:16px system-ui;margin:32px;color:#172334}table{border-collapse:collapse;margin:24px 0}'
            'td,th{padding:10px;border:1px solid #ccd3dd;text-align:right}th{background:#edf2f7}'
            'p{max-width:1000px}a{color:#165cb8}</style><h1>ChatGPT 连接复用：三轮 PDF 对照</h1>'
            f'<p>模型：{escape(result["model"])}；状态：{escape(result["status"])}。未晋升基准；正式质量评审未完成。</p>'
            '<p>reduction_percent 为耗时缩短百分比，正数表示候选更快。连接握手累计时间不能直接视为整体节省时间。</p>'
            f'<p>完整性问题：{escape(str(result["integrity_issues"]))}</p>')
    html += table(result['pairs'], ['round','cache','before_seconds','after_seconds','reduction_percent','same_payloads'])
    html += table(result['runs'], ['directory','seconds','calls','input_tokens','output_tokens','cached_input_tokens',
                                  'connections','request_p50_s','request_p95_s','failed_calls','retries','cache_hits'])
    html += '<p>机器可读结果：<a href="comparison-summary.json">JSON</a> · <a href="comparison-runs.csv">CSV</a></p></html>'
    (output / 'index.html').write_text(html, encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = report(args.directory, args.output)
    print(f'{len(result["pairs"])} pairs; {len(result["integrity_issues"])} integrity issues')
