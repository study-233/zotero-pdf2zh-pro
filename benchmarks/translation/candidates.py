"""Source-anchored diagnostic variants, isolated from the frozen quality corpus."""
from __future__ import annotations

import copy
import re
from pathlib import Path

from .common import digest, file_hash, inside, read, rows
from .corpus import normalize


def load(data, recipe):
    data, recipe = Path(data), read(recipe)
    if recipe.get('schema') != 1 or not recipe.get('version') or not recipe.get('cases'):
        raise ValueError('Candidate recipe requires schema 1, a version and cases')
    samples = {s['id']: s for s in rows(data / 'samples.jsonl')}
    sources = {s['id']: s for s in read(data / 'sources.json')}
    result, ids, groups = [], set(), set()
    for case in recipe['cases']:
        group = case['id']
        if not re.fullmatch(r'[a-z0-9_-]+', group) or group in groups or not case.get('review_points'):
            raise ValueError('Candidate groups must be unique and have review points')
        groups.add(group)
        source = case['source']
        if 'sample_id' in source:
            sample = copy.deepcopy(samples[source['sample_id']])
        else:
            import fitz
            paper = sources[source['paper']]
            path = inside(data, paper['pdf'])
            if file_hash(path) != paper['sha256']:
                raise ValueError('Candidate source PDF hash mismatch')
            with fitz.open(path) as pdf:
                text = normalize(pdf[source['page'] - 1].get_text('blocks')[source['block']][4])
            prefix = source.get('strip_prefix', '')
            if not text.startswith(prefix):
                raise ValueError('Candidate source prefix mismatch')
            sample = {'source_text': text[len(prefix):].strip(), 'paper': paper['id'],
                      'domain': paper['domain'], 'split': paper['split'], 'origin': 'real',
                      'source_locations': [source], 'must_preserve': []}
        if digest(sample['source_text'].encode()) != source['sha256']:
            raise ValueError('Candidate source text hash mismatch')
        if len(case.get('variants', [])) < 2:
            raise ValueError('Candidate diagnostics require a control and a variant')
        seen_inputs = set()
        for variant in case['variants']:
            if set(variant) - {'id', 'change', 'line_break_before', 'glossary_additions'}:
                raise ValueError('Unsupported candidate variant field')
            name = variant['id']
            sid = group + '--' + name
            if not re.fullmatch(r'[a-z0-9_-]+', name) or sid in ids:
                raise ValueError('Candidate variant IDs must be unique')
            ids.add(sid)
            item = copy.deepcopy(sample)
            for literal in variant.get('line_break_before', []):
                if item['source_text'].count(literal) != 1:
                    raise ValueError('Line boundary must identify exactly one source occurrence')
                item['source_text'] = item['source_text'].replace(literal, '\n' + literal)
            if normalize(item['source_text']) != normalize(sample['source_text']):
                raise ValueError('Line restoration must not change source words')
            glossary = variant.get('glossary_additions', [])
            for entry in glossary:
                if not all(isinstance(entry.get(k), str) and entry[k].strip() for k in ('source', 'target')):
                    raise ValueError('Candidate glossary requires source and target')
            item['selection_glossary'] = item.get('selection_glossary', []) + glossary
            input_hash = digest([item['source_text'], item.get('selection_context', ''), item['selection_glossary']])
            if input_hash in seen_inputs:
                raise ValueError('Duplicate candidate inputs within a correlation group')
            seen_inputs.add(input_hash)
            item.update(id=sid, group='candidate:' + group, source_sha256=digest(item['source_text'].encode()),
                        candidate={'version': recipe['version'], 'group': group, 'variant': name,
                                   'source': source, 'review_points': case['review_points'],
                                   'change': variant.get('change', 'Original input; existing product context/glossary retained'),
                                   'status': 'diagnostic_only', 'finding': case.get('finding')})
            result.append(item)
    return {'version': recipe['version'], 'sha256': digest({'recipe': recipe, 'samples': result}),
            'groups': len(groups), 'samples': result}
