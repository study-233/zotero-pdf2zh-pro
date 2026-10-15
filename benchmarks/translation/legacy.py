"""Import historical evidence byte-for-byte, never silently rescore/rewrite it."""
from __future__ import annotations

import shutil
from pathlib import Path

from .common import HERE, file_hash, now, read, write

FILES = ('samples.jsonl', 'dataset_manifest.json', 'rubric.json', 'config.json', 'summary.json',
         'raw_results.jsonl', 'results.jsonl', 'combined_results.jsonl', 'reviews.jsonl', 'comparison.html',
         'smoke_results.jsonl', 'run_eval.py', 'build_report.py', 'review_tools.py')


def import_legacy(source, destination):
    source, destination = Path(source), Path(destination)
    spec = read(HERE / 'datasets' / 'legacy-v1' / 'manifest.json')
    if file_hash(source / 'samples.jsonl') != spec['text_samples_sha256']:
        raise ValueError('This is not the frozen historical 60-sample dataset')
    if destination.exists():
        raise ValueError('Legacy destination exists; historical evidence is immutable')
    destination.mkdir(parents=True)
    checksums = {}
    for name in FILES:
        path = source / name
        if path.is_file():
            shutil.copyfile(path, destination / name)
            checksums[name] = file_hash(path)
    write(destination / 'import.json', {'dataset': 'legacy-v1', 'imported_at': now(), 'files': checksums,
                                      'policy': 'Original prompts, scores and token accounting are historical evidence; do not relabel as v2.'})
    return {'files': len(checksums), 'dataset': 'legacy-v1'}
