#!/usr/bin/env python3
"""Prepare generated content and frozen public assets for the Next.js website."""
from html import unescape
import json
import re
import shutil

from build_site import ROOT, SITE, Website
from website_data import copy_benchmark_assets, load_benchmark
from sync_guide_docs import sync


def reset_generated(name):
    if name not in {'.generated-public', '.generated-content', '.generated-data'}:
        raise ValueError(f'Not a website generated directory: {name}')
    path = SITE / name
    if path.resolve() != SITE.resolve() / name or path.is_symlink():
        raise ValueError(f'Refusing to replace linked generated directory: {path}')
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def release_entries(site):
    """Split the authoritative parsed changelog, retaining every legacy heading ID."""
    tokens = site.changelog
    starts = [i for i, token in enumerate(tokens) if token.type == 'heading_open' and token.tag == 'h2']
    entries = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(tokens)
        match = re.fullmatch(r'(v[\d.]+)\s*[-–]\s*(\d{4}-\d{2}-\d{2})', tokens[start + 1].content)
        if not match:
            raise ValueError(f'Unrecognized release heading: {tokens[start + 1].content}')
        html = site.render(tokens[start + 3:end], 'CHANGELOG.md')
        paragraphs = re.findall(r'<p[^>]*>(.*?)</p>', html, re.S)
        summaries = [unescape(re.sub(r'<[^>]+>', '', p)).strip() for p in paragraphs]
        bullets = re.findall(r'<li[^>]*>(.*?)</li>', html, re.S)
        fallback = unescape(re.sub(r'<[^>]+>', '', bullets[0])).strip() if bullets else ''
        summary = next((p for p in summaries if p.startswith('本次更新')), fallback or (summaries[0] if summaries else ''))
        summary = re.split(r'(?<=[。！？])', summary, maxsplit=1)[0]
        if len(summary) > 100:
            summary = summary[:100].rstrip('，、；： ') + '…'
        entries.append({
            'version': match[1], 'date': match[2], 'summary': summary,
            'anchor': tokens[start].attrGet('id'),
            'anchors': re.findall(r'\bid="([^"]+)"', html), 'html': html,
        })
    return entries


def prepare():
    sync(check=True)
    site = Website()
    benchmark, overview = load_benchmark()
    public = reset_generated('.generated-public')
    data = reset_generated('.generated-data')
    copy_benchmark_assets(public, benchmark)
    # The Next.js root page replaces the historical HTML; historical assets remain.
    (public / 'index.html').unlink()
    shutil.copytree(SITE / 'static', public / 'site', dirs_exist_ok=True)
    changes = site.render(site.changelog, 'CHANGELOG.md')
    (data / 'changelog.html').write_text(changes, encoding='utf-8')
    (data / 'releases.json').write_text(json.dumps(release_entries(site), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    preparation = site.render(site.pages[0]['tokens'], site.pages[0]['source'])
    (data / 'preparation-anchors.json').write_text(json.dumps(re.findall(r'\bid="([^"]+)"', preparation), ensure_ascii=False), encoding='utf-8')
    for asset in site.assets:
        target = public / asset
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / asset, target)
    manifest = json.loads((ROOT / 'plugin/addon/manifest.json').read_text(encoding='utf-8'))
    metadata = {
        'version': site.version, 'base': site.base, 'repo': site.context()['repo'],
        'compatibility': manifest['applications']['zotero'],
        'benchmark': overview, 'screenshots': site.screenshots,
        'pages': [{key: page[key] for key in ('title', 'route', 'group')} for page in site.pages],
    }
    (data / 'site.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    # Keep the benchmark's verified data markup while React owns page chrome.
    benchmark_page = site.env.get_template('benchmark.html').render(**site.context(
        title='模型测评', route='benchmark/', kind='benchmark', description='',
        benchmark=overview, ui_icons={path.stem: path.read_text(encoding='utf-8')
                                    for path in (SITE / 'static/vendor/pricing-ui').glob('*.svg')}))
    benchmark_body = re.search(r'<main[^>]*class="benchmark-main benchmark-explorer"[^>]*>(.*?)</main>', benchmark_page, re.S)
    if not benchmark_body:
        raise ValueError('Benchmark template must retain its explorer boundary')
    (data / 'benchmark.json').write_text(json.dumps({'html': benchmark_body[1].replace('<h1>翻译模型对比</h1>', '')}, ensure_ascii=False), encoding='utf-8')
    print(f'Prepared {len(site.pages)} guide chapters, release metadata and immutable benchmark assets.')


if __name__ == '__main__':
    prepare()
