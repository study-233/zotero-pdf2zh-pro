"""Portable source retrieval and frozen, inspectable extraction recipes."""
from __future__ import annotations

import json
import re
import shutil
import tempfile
import unicodedata
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path

from .common import DATASET, DEFAULT_DATA, DOMAINS, HERE, LENGTHS, digest, file_hash, inside, read, rows, write


def fetch_sources(destination, *, sources=None):
    destination = Path(destination)
    sources = sources or read(HERE / 'datasets' / DATASET / 'sources.json')
    for source in sources:
        path = inside(destination, source['pdf'])
        if path.exists():
            if source.get('sha256') and file_hash(path) != source['sha256']:
                raise ValueError(f'Existing PDF hash mismatch: {source["id"]}')
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(source['url'], headers={'User-Agent': 'TranslationBenchmark/2 (source reproduction)'})
        with urllib.request.urlopen(request, timeout=60) as response:
            if response.status != 200:
                raise ValueError(f'Incomplete HTTP download: {source["id"]}')
            data = response.read(64 * 1024 * 1024 + 1)
        if not data.startswith(b'%PDF') or b'%%EOF' not in data[-2048:] or len(data) > 64 * 1024 * 1024:
            raise ValueError(f'Not a supported PDF: {source["id"]}')
        if source.get('sha256') and digest(data) != source['sha256']:
            raise ValueError(f'Download hash mismatch: {source["id"]}')
        path.write_bytes(data)
        print(f'fetched {source["id"]}', flush=True)


def normalize(text):
    text = unicodedata.normalize('NFKC', text).replace('\u00ad', '').replace('\x02', '')
    text = re.sub(r'([a-z])-\n([a-z])', r'\1\2', text)
    return re.sub(r'\s+', ' ', text).strip()


def extract_blocks(path):
    import fitz
    result = []
    with fitz.open(path) as document:
        for page_no, page in enumerate(document, 1):
            for index, block in enumerate(page.get_text('blocks', sort=False)):
                if len(block) > 6 and block[6] != 0:
                    continue
                text = normalize(block[4])
                result.append({'page': page_no, 'block': index, 'bbox': [round(x, 2) for x in block[:4]], 'text': text})
    return result


def materialize(destination, *, spec_dir=None):
    """Reconstruct exactly; changing an extractor cannot silently change a dataset."""
    destination = Path(destination)
    spec = Path(spec_dir or HERE / 'datasets' / DATASET)
    sources, recipes = read(spec / 'sources.json'), read(spec / 'recipes.json')
    blocks = {}
    for source in sources:
        path = inside(destination, source['pdf'])
        if file_hash(path) != source['sha256']:
            raise ValueError(f'Source mismatch: {source["id"]}')
        blocks[source['id']] = {(b['page'], b['block']): b for b in extract_blocks(path)}
    samples = []
    for recipe in recipes:
        sample = dict(recipe)
        parts = []
        for anchor in recipe['source_locations']:
            text = blocks[recipe['paper']][(anchor['page'], anchor['block'])]['text']
            if digest(text.encode()) != anchor['block_sha256']:
                raise ValueError(f'Extractor drift: {recipe["id"]}')
            parts.append(text[anchor.get('start', 0):anchor.get('end', len(text))])
        sample['source_text'] = '\n\n'.join(parts)
        if recipe.get('selection_context_page'):
            sample['selection_context'] = '\n'.join(b['text'] for b in blocks[recipe['paper']].values()
                if b['page'] == recipe['selection_context_page'])[:11000]
        if digest(sample['source_text'].encode()) != sample['source_sha256']:
            raise ValueError(f'Sample mismatch: {sample["id"]}')
        samples.append(sample)
    samples.extend(rows(spec / 'challenges.jsonl'))
    destination.mkdir(parents=True, exist_ok=True)
    (destination / 'samples.jsonl').write_text(''.join(json.dumps(s, ensure_ascii=False) + '\n' for s in samples), encoding='utf-8')
    for name in ('manifest.json', 'sources.json', 'rubric.json'):
        shutil.copyfile(spec / name, destination / name)
    return verify(destination)


def length_class(text):
    count = len(text.split())
    return next((name for name, (low, high) in LENGTHS.items() if low <= count <= high), None)


def dataset_fingerprint(sources, samples, rubric, manifest):
    return digest({'sources': sources,
                   'samples': [{k: v for k, v in s.items() if k not in {'source_text', 'selection_context'}} for s in samples],
                   'rubric': rubric, 'manifest': {k: v for k, v in manifest.items() if k != 'content_sha256'}})


def verify(directory, *, metadata_only=False):
    directory = Path(directory)
    manifest = read(directory / 'manifest.json')
    if manifest['dataset'] != DATASET:
        raise ValueError('Use legacy import/inspection for historical data')
    sources = read(directory / 'sources.json')
    if metadata_only:
        samples = read(directory / 'recipes.json') + rows(directory / 'challenges.jsonl')
    else:
        samples = rows(directory / 'samples.jsonl')
    errors = []
    def require(condition, message):
        if not condition:
            errors.append(message)
    require(len(sources) == 16 and len({s['id'] for s in sources}) == 16, '16 unique sources required')
    require(len(samples) == 240 and len({s['id'] for s in samples}) == 240, '240 unique samples required')
    require(Counter(s['domain'] for s in sources) == Counter({d: 2 for d in DOMAINS}), 'source domain quotas')
    require(Counter(s['domain'] for s in samples) == Counter({d: 30 for d in DOMAINS}), 'sample domain quotas')
    require(Counter(s['origin'] for s in samples) == Counter(real=160, challenge=80), 'origin quotas')
    for domain in DOMAINS:
        require(Counter(s['origin'] for s in samples if s['domain'] == domain) == Counter(real=20, challenge=10), f'{domain}: origin quotas')
        require(len({s['tags'][1] for s in samples if s['domain'] == domain and s['origin'] == 'challenge'}) == 10, f'{domain}: challenge category quotas')
    require(Counter(s['split'] for s in samples) == Counter(development=120, holdout=120), 'split quotas')
    require(Counter(s['length'] for s in samples) == Counter(short=48, paragraph=96, long=72, extended=24), 'length quotas')
    source_map = {s['id']: s for s in sources}
    families = {}
    texts = []
    for sample in samples:
        sid = sample['id']
        require(bool(sample.get('key_points')), f'{sid}: missing semantic anchors')
        require(bool(sample.get('tags')), f'{sid}: missing tags')
        require(bool(sample.get('group')), f'{sid}: missing group')
        require(bool(sample.get('difficulty')), f'{sid}: missing difficulty')
        if sample['origin'] == 'real':
            source = source_map.get(sample['paper'], {})
            require(source.get('split') == sample['split'], f'{sid}: document split leakage')
            require(bool(sample.get('source_locations')), f'{sid}: missing location')
        else:
            family = sample['template_family']
            require(families.setdefault(family, sample['split']) == sample['split'], f'{sid}: template split leakage')
        if 'source_text' in sample:
            text = sample['source_text']
            require(length_class(text) == sample['length'], f'{sid}: length mismatch')
            require(len(text.split()) == sample['word_count'], f'{sid}: word count mismatch')
            require(digest(text.encode()) == sample['source_sha256'], f'{sid}: source hash mismatch')
            if sample.get('selection_context_sha256'):
                require(digest(sample.get('selection_context', '').encode()) == sample['selection_context_sha256'], f'{sid}: context changed')
            for point in sample['key_points']:
                if isinstance(point, dict):
                    require(type(point.get('start')) is int and type(point.get('end')) is int and 0 <= point['start'] < point['end'] <= len(text) and bool(point.get('instruction')), f'{sid}: invalid semantic anchor')
            for token in sample['must_preserve']:
                require(token in text, f'{sid}: preservation marker absent')
            texts.append((sample, normalize(text).casefold()))
    # Deterministic word-shingle checks catch containment and close paraphrase/repetition.
    for i, (a, text_a) in enumerate(texts):
        wa = text_a.split()
        sa = set(zip(*(wa[j:] for j in range(5))))
        for b, text_b in texts[:i]:
            wb = text_b.split()
            sb = set(zip(*(wb[j:] for j in range(5))))
            similar = bool(sa and sb) and len(sa & sb) / min(len(sa), len(sb)) >= .8
            if text_a in text_b or text_b in text_a or similar:
                require(a['group'] == b['group'] and a['split'] == b['split'], f'unmarked duplicate/leakage: {a["id"]}, {b["id"]}')
    by_id = {s['id']: s for s in samples}
    for name, count in [('quick_ids', 24), ('repeat_ids', 20), ('selection_ids', 24)]:
        values = manifest[name]
        require(len(values) == len(set(values)) == count and all(x in by_id for x in values), f'{name}: invalid IDs')
    require(all(by_id[x]['split'] == 'development' for x in manifest['quick_ids']), 'quick set must not consume holdout')
    require(len(manifest['pdf_quick']) == 4, 'four quick pages required')
    require(sum(len(s['anchors']) for s in sources) == 48, '48 PDF anchors required')
    for source in sources:
        require(len({a['page'] for a in source['anchors']}) == 3, f'{source["id"]}: duplicate page anchors')
        require(all(1 <= a['page'] <= source['pages'] for a in source['anchors']), f'{source["id"]}: invalid page anchor')
        if not metadata_only:
            path = inside(directory, source['pdf'])
            require(path.is_file() and file_hash(path) == source['sha256'], f'{source["id"]}: PDF missing/mismatch')
    fingerprint = dataset_fingerprint(sources, samples, read(directory / 'rubric.json'), manifest)
    require(fingerprint == manifest['content_sha256'], 'dataset/rubric changed without a version update')
    if errors:
        raise ValueError('\n'.join(errors))
    return {'dataset': DATASET, 'samples': len(samples), 'pdfs': len(sources), 'anchors': 48, 'content_sha256': fingerprint, 'materialized': not metadata_only}


def export_package(directory, archive):
    verify(directory)
    directory, archive = Path(directory), Path(archive)
    if archive.exists():
        raise ValueError('Export destination already exists')
    allowed = ['manifest.json', 'sources.json', 'rubric.json', 'samples.jsonl'] + [s['pdf'] for s in read(directory / 'sources.json')]
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as package:
        for relative in allowed:
            package.write(inside(directory, relative), relative)


def import_package(archive, destination):
    destination = Path(destination)
    if destination.exists():
        raise ValueError('Import destination already exists')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        root = Path(temporary) / 'data'
        root.mkdir()
        with zipfile.ZipFile(archive) as package:
            if sum(i.file_size for i in package.infolist()) > 1024 * 1024 * 1024:
                raise ValueError('Package exceeds 1 GiB')
            names = set()
            for member in package.infolist():
                if member.filename in names or (member.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError('Duplicate or symbolic-link archive entry')
                names.add(member.filename)
                target = inside(root, member.filename)
                if member.is_dir():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with package.open(member) as source, target.open('wb') as output:
                    shutil.copyfileobj(source, output)
        verify(root)
        root.rename(destination)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('fetch', 'materialize', 'verify'))
    parser.add_argument('--data', type=Path, default=DEFAULT_DATA / DATASET)
    args = parser.parse_args()
    if args.action == 'fetch':
        fetch_sources(args.data)
    else:
        print(materialize(args.data) if args.action == 'materialize' else verify(args.data))
