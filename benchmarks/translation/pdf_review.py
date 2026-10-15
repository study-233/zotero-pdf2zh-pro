"""Explicit page-anchor review; a rendered file is not evidence of translation quality."""
from __future__ import annotations

import statistics
import random
from pathlib import Path

from .common import DIMENSIONS, digest, file_hash, read, rows, write


def prepare(run, destination):
    import fitz
    run, destination = Path(run), Path(destination)
    if destination.exists():
        raise ValueError('PDF review destination exists')
    samples = read(run / 'samples.json')
    destination.mkdir(parents=True)
    items, mapping, candidates = [], {}, []
    for row in rows(run / 'results.jsonl'):
        if any(file_hash(run / artifact['path']) != artifact['sha256'] for artifact in row.get('artifacts', [])):
            raise ValueError('PDF artifact changed after execution')
        mono = next((a for a in row.get('artifacts', []) if a['mode'] == 'mono'), None)
        if mono:
            candidates.extend((row, mono, anchor) for anchor in samples[row['sample_id']]['anchors'])
    random.Random(61013).shuffle(candidates)
    for row, mono, anchor in candidates:
        with fitz.open(run / row['input_pdf']) as source, fitz.open(run / mono['path']) as translated:
            if len(source) != len(translated):
                raise ValueError(f'{row["sample_id"]}: page count differs; map pages before review')
            page = 1 if len(source) == 1 else anchor['page']
            opaque = f'P{len(items)+1:04}'
            for side, document in [('source', source), ('translation', translated)]:
                document[page-1].get_pixmap(matrix=fitz.Matrix(1.25, 1.25)).save(destination / f'{opaque}-{side}.png')
            dual_images = []
            dual = next((a for a in row.get('artifacts', []) if a['mode'] == 'dual'), None)
            if dual:
                with fitz.open(run / dual['path']) as bilingual:
                    if len(bilingual) not in {len(source), len(source)*2}:
                        raise ValueError('Bilingual page count requires an explicit page mapping')
                    positions = [page-1] if len(bilingual) == len(source) else [(page-1)*2, (page-1)*2+1]
                    for n, position in enumerate(positions):
                        name = f'{opaque}-dual-{n+1}.png'
                        bilingual[position].get_pixmap(matrix=fitz.Matrix(1.25, 1.25)).save(destination / name)
                        dual_images.append(name)
            items.append({'id': opaque, 'source_page': anchor['page'], 'source': source[page-1].get_text(),
                          'translation': translated[page-1].get_text(), 'role': anchor['role'],
                          'source_image': opaque+'-source.png', 'translation_image': opaque+'-translation.png',
                          'dual_images': dual_images})
            mapping[opaque] = {'job_id': row['job_id'], 'result_sha256': digest(row)}
    packet = {'items': items, 'rubric': read(run / 'rubric.json')}
    write(destination / 'packet.json', packet)
    write(destination / 'private-key.json', {'run': str(run.resolve()), 'packet_sha256': digest(packet), 'items': mapping})
    write(destination / 'review-template.json', {'packet_sha256': digest(packet), 'reviewer': '', 'reviewer_version': '', 'independent': False,
          'items': [{'id': i['id'], 'scores': {d: None for d in DIMENSIONS}, 'content_completeness': None,
                     'layout_readability': None, 'errors': [], 'layout_evidence': '', 'content_evidence': ''} for i in items]})
    return {'pages': len(items), 'packet': str(destination / 'packet.json')}


def import_reviews(packet_dir, review_file):
    packet, verdicts = read(Path(packet_dir) / 'packet.json'), read(review_file)
    key = read(Path(packet_dir) / 'private-key.json')
    if key['packet_sha256'] != digest(packet):
        raise ValueError('PDF packet changed')
    if digest(packet) != verdicts.get('packet_sha256') or not verdicts.get('reviewer') or not verdicts.get('reviewer_version') or type(verdicts.get('independent')) is not bool:
        raise ValueError('PDF review identity or packet is invalid')
    by_id = {i['id']: i for i in packet['items']}
    if len(verdicts['items']) != len(by_id) or {v['id'] for v in verdicts['items']} != set(by_id):
        raise ValueError('Every PDF anchor must be reviewed exactly once')
    grouped = {}
    for verdict in verdicts['items']:
        item = by_id[verdict['id']]
        scores = verdict['scores']
        if set(scores) != set(DIMENSIONS) or any(type(v) is not int or not 0 <= v <= 4 for v in [*scores.values(), verdict['content_completeness'], verdict['layout_readability']]):
            raise ValueError('PDF scores require integers 0-4')
        evidence = set()
        for error in verdict['errors']:
            if error.get('dimension') not in DIMENSIONS or error.get('severity') not in {'minor', 'major', 'critical'} or not error.get('explanation'):
                raise ValueError('Invalid PDF error evidence')
            if not error.get('source_quote') or error['source_quote'] not in item['source']:
                raise ValueError('Missing source evidence')
            target = error.get('target_quote')
            if not isinstance(target, str) or target not in item['translation'] or not target and error.get('category') != 'omission':
                raise ValueError('Missing target evidence')
            evidence.add(error['dimension'])
        if any(v < 4 and d not in evidence for d, v in scores.items()):
            raise ValueError('PDF translation deductions require evidence')
        if not verdict.get('layout_evidence') or not verdict.get('content_evidence'):
            raise ValueError('Content and layout observations are required even for a passing page')
        grouped.setdefault(key['items'][item['id']]['job_id'], []).append(({**item, **key['items'][item['id']]}, verdict))
    run = Path(key['run'])
    if digest(read(run / 'rubric.json')) != digest(packet['rubric']):
        raise ValueError('PDF rubric changed')
    results = {r['job_id']: r for r in rows(run / 'results.jsonl')}
    reviews = {}
    for job, values in grouped.items():
        if any(item['result_sha256'] != digest(results[job]) for item, _ in values):
            raise ValueError('PDF result changed after review preparation')
        scores = {d: statistics.mean(v['scores'][d] for _, v in values) for d in DIMENSIONS}
        reviews[job] = {'scores': scores, 'score': sum(scores[d]*w/4 for d,w in DIMENSIONS.items()),
                        'errors': [e for _, v in values for e in v['errors']], 'result_sha256': digest(results[job]),
                        'reviewer': verdicts['reviewer'], 'reviewer_version': verdicts['reviewer_version'],
                        'independent': verdicts['independent'], 'content_completeness': statistics.mean(v['content_completeness'] for _, v in values),
                        'layout_readability': statistics.mean(v['layout_readability'] for _, v in values), 'anchors_reviewed': len(values)}
    if (run / 'reviews.json').exists():
        raise ValueError('PDF review already exists; preserve it before starting a new versioned round')
    write(run / 'reviews.json', reviews)
    write(run / 'pdf-review.json', verdicts)
    return {'reviewed_pages': len(verdicts['items']), 'documents': len(reviews)}
