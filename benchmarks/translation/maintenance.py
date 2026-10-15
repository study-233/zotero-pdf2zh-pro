"""Offline, versioned review closeouts. Never load a provider or promote a baseline."""
from __future__ import annotations

import csv
import html
from pathlib import Path

from .common import DIMENSIONS, ROOT, digest, file_hash, inside, read, rows, write


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _hash(root, path, expected):
    _require(file_hash(inside(root, path)) == expected, f'Hash mismatch: {path}')


def verify(records, evidence_root=None):
    """Metadata-only works in CI; evidence mode also reconciles the private run files."""
    records = Path(records)
    manifest = read(records / 'manifest.json')
    _require(manifest['schema'] == 1 and manifest['status'] == 'engineering-review-only', 'Unsupported closeout')
    _require(manifest['live_calls'] == 0 and manifest['baseline_promoted'] is False, 'Closeout must stay offline')
    _require(set(manifest['files']) == {'inventory.json', 'exposure.json', 'candidates.json', 'frozen.json',
                                      'calibration.json', 'preservation.json', 'visual-check.json'}, 'Incomplete closeout files')
    for name, expected in manifest['files'].items():
        _hash(records, name, expected)
    inventory = read(records / 'inventory.json')
    exposure = read(records / 'exposure.json')
    candidates = read(records / 'candidates.json')
    frozen = read(records / 'frozen.json')
    for path, expected in frozen.items():
        _hash(ROOT, path, expected)
    recipe = read(ROOT / 'benchmarks/translation/datasets/cs-en-zh-v2/recipes.json')
    sources = {s['id']: s for s in read(ROOT / 'benchmarks/translation/datasets/cs-en-zh-v2/sources.json')}
    runs = {r['run']: r for r in inventory}
    _require(len(runs) == len(inventory) == len({r['run_id'] for r in inventory}), 'Duplicate run')
    for r in inventory:
        inside(records, r['run'])  # Validate relocatable paths even without private evidence.
        for path in r['files']:
            inside(records, path)
        _require(r['unreviewed_outputs'] == 0, f"Missing review: {r['run']}")
        _require(r['reviewed_outputs'] + r['no_output_results'] == r['main_results'], 'Output accounting mismatch')
    for e in exposure['holdout_exposures']:
        paper = e['paper']
        _require(sources[paper]['split'] == 'holdout', 'Exposure must identify a holdout paper')
        _require(sources[paper]['sha256'] == e['source_sha256'], 'Exposure source drift')
        _require(e['original_split_unchanged'] is True, 'Do not rewrite the frozen split')
        expected = sorted(s['id'] for s in recipe if s['paper'] == paper)
        _require(e['paper_level_affected_sample_ids'] == expected, 'Incomplete paper-level exposure')
        overlap = sorted(s['id'] for s in recipe if s['paper'] == paper and
                         any(loc['page'] in e['pages'] for loc in s['source_locations']))
        _require(e['direct_overlap_sample_ids'] == overlap, 'Incorrect page overlap')
        _require(e['evidence'], 'Exposure needs evidence')
    ids = [c['id'] for c in candidates['cases']]
    _require(len(set(ids)) == len(ids), 'Duplicate candidate')
    for c in candidates['cases']:
        _require(c['status'] == 'candidate' and c['expected'] and c['correlation_group'] and c['evidence'], 'Incomplete candidate')
        _require(c['lane'] in {'translation', 'layout', 'annotation', 'workflow'}, 'Unknown candidate lane')
    references = [e for c in candidates['cases'] for e in c['evidence']]
    references += [e for c in exposure['holdout_exposures'] for e in c['evidence']]
    for e in references:
        _require(e['run'] in runs and e['job_id'] and len(e['result_sha256']) == 64, 'Unknown evidence reference')
    checked_files = 0
    if evidence_root is not None:
        from .review import audit
        evidence_root = Path(evidence_root)
        actual = {}
        for r in inventory:
            run = inside(evidence_root, r['run'])
            for path, expected in r['files'].items():
                _hash(run, path, expected)
                checked_files += 1
            meta = read(run / 'run.json')
            result = rows(run / 'results.jsonl')
            _require(len({x['job_id'] for x in result}) == len(result), 'Duplicate result job')
            actual[r['run']] = {x['job_id']: x for x in result}
            _require(meta['run_id'] == r['run_id'], 'Run identity changed')
            main = [x for x in result if x['phase'] == 'main']
            eligible = {x['job_id'] for x in main if x.get('translation') or x.get('artifacts')}
            reviews = read(run / 'reviews.json') if (run / 'reviews.json').exists() else {}
            _require(set(reviews) == eligible, 'Review coverage mismatch')
            _require(len(main) == r['main_results'] and len(reviews) == r['reviewed_outputs'], 'Review count drift')
            _require(len(set(meta['planned_jobs']) - set(actual[r['run']])) == r['unexecuted_jobs'], 'Unexecuted count drift')
            for job, review in reviews.items():
                _require(review['result_sha256'] == digest(actual[r['run']][job]), 'Review/result mismatch')
                score = sum(review['scores'][d] * w / 4 for d, w in DIMENSIONS.items())
                _require(score == review['score'], 'Weighted score mismatch')
                _require(all(type(v) is int and 0 <= v <= 4 for v in review['scores'].values()), 'Invalid dimension score')
                for artifact in actual[r['run']][job].get('artifacts', []):
                    _hash(run, artifact['path'], artifact['sha256'])
            if reviews:
                expected = audit(run)
                supplied = read(run / 'audit.json')
                _require(supplied['review_sha256'] == expected['review_sha256'], 'Stale audit')
                verdicts = supplied['verdicts']
                _require(set(verdicts) == set(expected['required_job_ids']), 'Audit coverage mismatch')
                _require(all(v['status'] == 'confirmed' and v['note'].strip() for v in verdicts.values()), 'Unconfirmed audit')
                _require(supplied.get('reviewer') and supplied.get('independent') is False, 'Declare non-independent audit')
                _require(len(verdicts) == r['audited_outputs'], 'Audit count drift')
        for e in references:
            row = actual[e['run']].get(e['job_id'])
            _require(row is not None and digest(row) == e['result_sha256'], 'Candidate/exposure result drift')
            if 'error_index' in e:
                review = read(inside(evidence_root, e['run']) / 'reviews.json')[e['job_id']]
                _require(digest(review['errors'][e['error_index']]) == e['error_sha256'], 'Candidate error drift')
            if 'review_field' in e:
                review = read(inside(evidence_root, e['run']) / 'reviews.json')[e['job_id']]
                _require(e['review_field'] in review, 'Missing candidate review evidence')
        for original in read(records / 'preservation.json')['archived_originals']:
            _hash(evidence_root, original['archive'], original['sha256'])
        for path, expected in read(records / 'visual-check.json')['artifacts'].items():
            _hash(evidence_root, path, expected)
    return {'version': manifest['version'], 'runs': len(inventory),
            'reviewed_outputs': sum(r['reviewed_outputs'] for r in inventory),
            'audited_outputs': sum(r['audited_outputs'] for r in inventory),
            'candidate_cases': len(ids), 'exposed_holdout_papers': len(exposure['holdout_exposures']),
            'evidence_verified': evidence_root is not None, 'checked_files': checked_files,
            'live_calls': 0, 'baseline_promoted': False}


def report(records, evidence_root, output):
    """Generate a new self-contained index plus the existing detailed review report."""
    from .report import report as detailed_report, summarize
    records, evidence_root, output = Path(records), Path(evidence_root), Path(output)
    _require(not output.exists(), 'Report output already exists; use a new directory')
    verification = verify(records, evidence_root)
    inventory = read(records / 'inventory.json')
    exposure = read(records / 'exposure.json')
    candidates = read(records / 'candidates.json')
    summaries = []
    for r in inventory:
        s = summarize(inside(evidence_root, r['run']))
        # No PDF artifact is N/A, not an unreviewed output or a fabricated zero score.
        s['unreviewed'] = r['unreviewed_outputs']
        s['review_status'] = 'reviewed' if r['reviewed_outputs'] else 'no-output-not-applicable'
        for field in ('items', 'configuration', 'execution_metrics'):
            s.pop(field, None)
        summaries.append({**s, **r})
    output.mkdir(parents=True)
    write(output / 'summary.json', {'verification': verification, 'runs': summaries,
                                   'exposure': exposure, 'candidates': candidates})
    fields = ['run', 'role', 'suite', 'execution_status', 'complete', 'main_results', 'reviewed_outputs',
              'audited_outputs', 'no_output_results', 'unexecuted_jobs', 'quality_score',
              'reviewed_subset_score', 'serious_error_rate', 'success_rate', 'usage_complete']
    with (output / 'summary.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(summaries)
    detailed_report([inside(evidence_root, r['run']) for r in inventory if r['reviewed_outputs']], output / 'details')
    esc = lambda value: html.escape(str(value))
    def number(v):
        return '—' if v is None else f'{v:.2f}'
    table = ''.join('<tr>' + ''.join(f'<td>{esc(v)}</td>' for v in [
        r['run'], r['role'], r['execution_status'], f"{r['reviewed_outputs']} / {r['audited_outputs']}",
        r['no_output_results'], r['unexecuted_jobs'], number(r['quality_score']),
        number(r['reviewed_subset_score']), number(None if r['serious_error_rate'] is None else 100*r['serious_error_rate']),
        '—' if not r['pdf_quality'] else f"{number(r['pdf_quality']['content_completeness'])} / {number(r['pdf_quality']['layout_readability'])}"
    ]) + '</tr>' for r in summaries)
    issues = ''.join(f"<li><b>{esc(c['title'])}</b>（{esc(c['lane'])}）：{esc(c['expected'])}</li>" for c in candidates['cases'])
    exposure_html = ''.join(f"<p>{esc(e['paper'])} 第 {esc(e['pages'])} 页已暴露，直接重叠 {esc(', '.join(e['direct_overlap_sample_ids']))}；保守标记整篇 {len(e['paper_level_affected_sample_ids'])} 条，原 split 不变。后续不得宣称独立未见验收。</p>" for e in exposure['holdout_exposures'])
    document = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>基准评审维护记录</title>
<style>body{{font:16px system-ui;max-width:1500px;margin:32px auto;padding:20px;color:#172132;background:#f5f7fa}}table{{border-collapse:collapse;font-size:13px;width:100%}}td,th{{border:1px solid #ccd4df;padding:8px;text-align:left}}td:first-child{{overflow-wrap:anywhere;max-width:350px}}section{{background:white;padding:20px;margin:20px 0;border-radius:12px}}li{{margin:12px 0}}</style>
<h1>基准评审与维护 · {esc(verification['version'])}</h1>
<p>{verification['runs']} 份运行记录 · {verification['reviewed_outputs']} 个输出已评分 · {verification['audited_outputs']} 项复核 · 零新增模型调用 · 未晋升基准</p>
<section><h2>评审边界</h2><p>由执行助手完成工程评阅与第二遍复核，非独立盲评。所有严重问题和其余固定随机 20% 已复核。预检不计质量样本，缓存复用、重复页面和候选变体不增加独立证据数。不同配置、通道和实验分列，不汇总排名。</p>
<p>这是历史结果的维护报告，不构成正式 model / pdf-full 验收或基准晋升。</p>{exposure_html}
<p>失败运行的部分译文也已评阅；其子集评分不是验收通过。无产物为不适用，未执行项单列；用量未知保持未知。PDF 翻译评分与内容/排版（0–4）分开。详细报告中的单次分数不代表性能或质量提升。</p>
<p><a href="details/index.html">原文、译文、评分证据与运行指标</a> · <a href="summary.json">JSON</a> · <a href="summary.csv">CSV</a></p></section>
<section><h2>运行与复核</h2><table><thead><tr><th>运行</th><th>范围</th><th>执行状态</th><th>评分 / 复核</th><th>无产物</th><th>未执行</th><th>完整运行译文分</th><th>已评子集分</th><th>严重问题 %</th><th>PDF 内容 / 排版</th></tr></thead><tbody>{table}</tbody></table></section>
<section><h2>候选回归（未纳入冻结集）</h2><ul>{issues}</ul></section></html>'''
    (output / 'index.html').write_text(document, encoding='utf-8')
    return {**verification, 'report': str(output / 'index.html')}
