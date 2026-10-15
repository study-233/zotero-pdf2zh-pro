"""Evidence-backed, anonymous review and explicit baseline promotion."""
from __future__ import annotations

import math
import random
from pathlib import Path

from .common import DIMENSIONS, digest, now, read, rows, write


def prepare(runs, destination, seed=61013):
    destination = Path(destination)
    if destination.exists():
        raise ValueError('Review destination already exists')
    packet, mapping = [], {}
    rubric = None
    for path in map(Path, runs):
        manifest, samples = read(path / 'run.json'), read(path / 'samples.json')
        current = read(path / 'rubric.json')
        if rubric is not None and digest(rubric) != digest(current):
            raise ValueError('Prepare separate review packets for different rubric versions')
        rubric = current
        for row in rows(path / 'results.jsonl'):
            if row['phase'] != 'main' or not row['translation']:
                continue
            sample = samples[row['sample_id']]
            packet.append(({'source': sample['source_text'], 'translation': row['translation'],
                            'key_points': [{**p, 'source_evidence': sample['source_text'][p['start']:p['end']]} if isinstance(p, dict) else p for p in sample.get('key_points', [])], 'acceptable_terms': sample.get('acceptable_terms', {}),
                            'must_preserve': sample.get('must_preserve', []), 'rules': row['rules'],
                            'selection_context': sample.get('selection_context', ''),
                            'selection_glossary': sample.get('selection_glossary', []),
                            'candidate_review_points': sample.get('candidate', {}).get('review_points', []),
                            'candidate_change': sample.get('candidate', {}).get('change')},
                           {'run_id': manifest['run_id'], 'run': str(path.resolve()), 'job_id': row['job_id'],
                            'result_sha256': digest(row), 'rubric_sha256': digest(read(path / 'rubric.json'))}))
    random.Random(seed).shuffle(packet)
    entries = []
    for index, (item, identity) in enumerate(packet, 1):
        opaque = f'E{index:05d}'
        entries.append({'id': opaque, **item})
        mapping[opaque] = identity
    destination.mkdir(parents=True)
    write(destination / 'rubric.json', rubric)
    write(destination / 'packet.json', {'schema': 2, 'seed': seed, 'items': entries})
    write(destination / 'private-key.json', {'packet_sha256': digest(entries), 'items': mapping})
    write(destination / 'review-template.json', {'reviewer': '', 'reviewer_version': '', 'independent': False,
          'packet_sha256': digest(entries), 'items': [{'id': e['id'], 'scores': {d: None for d in DIMENSIONS},
          'errors': [], 'notes': ''} for e in entries]})
    return {'items': len(entries), 'packet': str(destination / 'packet.json')}


def import_reviews(packet_dir, review_file):
    packet_dir = Path(packet_dir)
    packet, key, review = read(packet_dir / 'packet.json'), read(packet_dir / 'private-key.json'), read(review_file)
    items = {item['id']: item for item in packet['items']}
    expected = digest(packet['items'])
    if review.get('packet_sha256') != expected or key['packet_sha256'] != expected:
        raise ValueError('Review packet changed')
    if not review.get('reviewer') or not review.get('reviewer_version') or type(review.get('independent')) is not bool:
        raise ValueError('Reviewer, version and independence declaration are required')
    imported, seen = {}, set()
    for verdict in review['items']:
        opaque = verdict['id']
        if opaque in seen or opaque not in items:
            raise ValueError('Unknown or duplicate review ID')
        seen.add(opaque)
        scores, sample = verdict['scores'], items[opaque]
        if set(scores) != set(DIMENSIONS) or any(type(v) is not int or not 0 <= v <= 4 for v in scores.values()):
            raise ValueError(f'{opaque}: all five dimensions require integer scores 0-4')
        evidence_dimensions = set()
        for error in verdict.get('errors', []):
            if error.get('dimension') not in DIMENSIONS or error.get('severity') not in {'minor', 'major', 'critical'} or not error.get('explanation'):
                raise ValueError(f'{opaque}: error dimension/severity/explanation required')
            if not error.get('source_quote') or error['source_quote'] not in sample['source']:
                raise ValueError(f'{opaque}: source evidence must occur in the source')
            target = error.get('target_quote')
            if not isinstance(target, str) or (target and target not in sample['translation']) or (not target and error.get('category') != 'omission'):
                raise ValueError(f'{opaque}: translation evidence or an explicit omission is required')
            evidence_dimensions.add(error['dimension'])
            for secondary in error.get('secondary_impacts', []):
                if secondary.get('dimension') not in DIMENSIONS or not secondary.get('explanation'):
                    raise ValueError('Secondary deductions require a distinct explanation')
                evidence_dimensions.add(secondary['dimension'])
        if any(value < 4 and dimension not in evidence_dimensions for dimension, value in scores.items()):
            raise ValueError(f'{opaque}: every deduction requires evidence')
        if sample['rules']['hard_failures'] and scores['structure_preservation'] == 4 and not verdict.get('rule_override_reason'):
            raise ValueError(f'{opaque}: unresolved hard preservation check')
        target = key['items'][opaque]
        run = Path(target['run'])
        actual = next(r for r in rows(run / 'results.jsonl') if r['job_id'] == target['job_id'])
        if digest(actual) != target['result_sha256'] or digest(read(run / 'rubric.json')) != target['rubric_sha256']:
            raise ValueError('Result or rubric changed after review preparation')
        stored = imported.setdefault(run, read(run / 'reviews.json') if (run / 'reviews.json').exists() else {})
        if target['job_id'] in stored:
            raise ValueError('Review already imported; use a new versioned review round rather than overwrite')
        stored[target['job_id']] = {**verdict, 'reviewer': review['reviewer'], 'reviewer_version': review['reviewer_version'],
                                   'independent': review['independent'], 'result_sha256': target['result_sha256'],
                                   'rubric_sha256': target['rubric_sha256'], 'imported_at': now(),
                                   'score': sum(scores[d] * w / 4 for d, w in DIMENSIONS.items())}
    for run, stored in imported.items():
        write(run / 'reviews.json', stored)
    return {'imported': len(seen), 'runs': len(imported)}


def audit(run, seed=61013):
    run = Path(run)
    reviews = read(run / 'reviews.json') if (run / 'reviews.json').exists() else {}
    required = {job for job, review in reviews.items() if any(e['severity'] in {'major', 'critical'} for e in review['errors'])}
    other = sorted(set(reviews) - required)
    required.update(random.Random(seed).sample(other, math.ceil(len(other) * .2)))
    return {'seed': seed, 'required_job_ids': sorted(required), 'review_sha256': digest(reviews)}


def new_round(run, name):
    """Archive the current complete review/audit set before accepting replacements."""
    run = Path(run)
    if not name or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in name):
        raise ValueError('Review round name must be a simple identifier')
    destination = run / 'review-history' / name
    files = [run / n for n in ('reviews.json', 'audit.json', 'pdf-review.json') if (run / n).exists()]
    if destination.exists() or not files:
        raise ValueError('Round name exists or there is no review to archive')
    destination.mkdir(parents=True)
    for path in files:
        path.rename(destination / path.name)
    write(destination / 'round.json', {'archived_at': now(), 'name': name})
    return {'archived': str(destination), 'active_review': 'unreviewed'}


def import_audit(run, audit_file):
    run, supplied = Path(run), read(audit_file)
    expected = audit(run)
    if supplied.get('review_sha256') != expected['review_sha256'] or not supplied.get('reviewer'):
        raise ValueError('Audit must identify the current review set and its reviewer')
    verdicts = supplied.get('verdicts', {})
    if set(verdicts) != set(expected['required_job_ids']) or any(v.get('status') != 'confirmed' or not v.get('note') for v in verdicts.values()):
        raise ValueError('Every required audit must be confirmed with a note; disputed reviews require a new round')
    write(run / 'audit.json', {**supplied, 'imported_at': now()})


def promote(run, registry, name):
    from .report import summarize
    run, registry = Path(run), Path(registry)
    summary = summarize(run)
    manifest = read(run / 'run.json')
    if manifest['identity'].get('candidates'):
        raise ValueError('Candidate diagnostics cannot be promoted')
    if manifest['status'] != 'completed' or manifest['identity']['suite'] not in {'model', 'pdf-full'}:
        raise ValueError('Only a completed formal model/PDF run can be promoted')
    if summary['unreviewed'] or summary.get('unknown_outcomes') or not summary['complete']:
        raise ValueError('Incomplete run, unknown outcomes or missing review blocks promotion')
    if not (run / 'audit.json').exists() or read(run / 'audit.json').get('review_sha256') != audit(run)['review_sha256']:
        raise ValueError('Current review audit is required')
    if manifest['identity']['suite'] == 'pdf-full':
        reviews = read(run / 'reviews.json')
        samples = read(run / 'samples.json')
        if not (run / 'pdf-review.json').exists() or any(reviews.get(row['job_id'], {}).get('anchors_reviewed') != len(samples[row['sample_id']]['anchors']) for row in rows(run / 'results.jsonl')):
            raise ValueError('Every PDF content/layout anchor must be reviewed before promotion')
    registry.mkdir(parents=True, exist_ok=True)
    if not name or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in name):
        raise ValueError('Baseline name must contain only letters, digits, dash and underscore')
    path = registry / (name + '.json')
    if path.exists():
        raise ValueError('Baseline names are immutable; choose a new name')
    write(path, {'name': name, 'promoted_at': now(), 'run_id': manifest['run_id'], 'run': str(run.resolve()),
                 'identity': manifest['identity'], 'summary': summary, 'results_sha256': digest(rows(run / 'results.jsonl')),
                 'reviews_sha256': digest(read(run / 'reviews.json'))})
    return str(path)
