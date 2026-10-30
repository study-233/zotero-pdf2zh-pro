#!/usr/bin/env python3
"""Freeze the legacy website once, then check migration URLs and immutable data."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import subprocess
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
BASE = '/zotero-pdf2zh-pro/'
SOURCE_TREES = ('benchmarks/translation/site', 'benchmarks/translation/publications')


class Anchors(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.ids = set()
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get('id'):
            self.ids.add(attrs['id'])
        if tag == 'a' and attrs.get('name'):
            self.ids.add(attrs['name'])


def fingerprint(path):
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'sha256': digest, 'bytes': path.stat().st_size}


def source_files(root):
    return {path.relative_to(root).as_posix(): fingerprint(path)
            for tree in SOURCE_TREES for path in sorted((root / tree).rglob('*'))
            if path.is_file()}


def capture(output, directory):
    # Only capture needs the legacy builder's dependencies. Validation is stdlib-only.
    from build_site import Website
    from check_docs import parse_document

    destinations = [directory / name for name in ('legacy-routes.json', 'legacy-assets.json')]
    if any(path.exists() for path in destinations):
        raise ValueError('Baseline already exists; do not replace it with migration output.')
    if not (output / 'index.html').is_file():
        raise ValueError('Build the legacy website before capturing the baseline.')
    site = Website(BASE)
    metadata = {
        'schema_version': 1,
        'captured_at': datetime.now(timezone.utc).isoformat(),
        'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'version': site.version,
        'base': BASE,
        'origin': 'https://study-233.github.io',
    }
    pages = {}
    for path in sorted(output.rglob('*.html')):
        relative = path.relative_to(output).as_posix()
        url = BASE + relative.removesuffix('index.html')
        pages[url] = sorted(Anchors(path.read_text(encoding='utf-8')).ids)
    targets = {url: url for url in pages}
    source_targets = {}
    for (source, anchor), target in sorted(site.anchors.items()):
        url = BASE + target
        targets[url] = url
        source_targets[source + '#' + anchor] = url
    # These are browser redirects, rather than IDs in the legacy home page.
    redirects = {BASE + '#' + anchor: BASE + 'benchmark/#' + anchor
                 for anchor in ('results', 'method', 'compare')}
    targets.update(redirects)
    source_documents = {
        source: sorted(parse_document((ROOT / source).read_text(encoding='utf-8')).anchors)
        for source in sorted(site.documents)
    }
    routes = {**metadata, 'pages': pages, 'targets': dict(sorted(targets.items())),
              'browser_redirects': redirects, 'source_targets': source_targets,
              'source_documents': source_documents}
    files = {}
    historical = ROOT / SOURCE_TREES[0]
    for path in sorted(output.rglob('*')):
        if not path.is_file():
            continue
        relative = path.relative_to(output).as_posix()
        immutable = (relative.startswith(('benchmark/data/', 'benchmark/pilot/', 'benchmark/experiment/'))
                     or ((historical / relative).is_file() and path.suffix in {'.json', '.csv', '.pdf'}))
        files[relative] = {**fingerprint(path), 'immutable': immutable}
    assets = {**metadata, 'files': files, 'source_files': source_files(ROOT)}
    errors = check(output, routes, assets)
    if errors:
        raise ValueError('\n'.join(errors))
    directory.mkdir(parents=True, exist_ok=True)
    for path, data in zip(destinations, (routes, assets)):
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Captured {len(pages)} pages, {len(targets)} URLs and {len(files)} output files.')


def check(output, routes, assets, root=ROOT):
    from check_docs import parse_document

    errors, pages = [], {}
    base = routes['base']
    if assets['base'] != base or assets['commit'] != routes['commit']:
        return ['Route and asset baselines must describe the same build.']
    for url in sorted(set(routes['targets'].values())):
        parsed = urlsplit(url)
        if parsed.scheme or parsed.netloc or not parsed.path.startswith(base):
            errors.append(f'Compatibility URL escaped project base: {url}')
            continue
        path = (output / unquote(parsed.path[len(base):])).resolve()
        if not path.is_relative_to(output.resolve()):
            errors.append(f'Compatibility URL escaped output directory: {url}')
            continue
        if path.is_dir():
            path /= 'index.html'
        if not path.is_file():
            errors.append(f'Missing compatibility page: {url}')
        elif parsed.fragment:
            if path not in pages:
                pages[path] = Anchors(path.read_text(encoding='utf-8')).ids
            if unquote(parsed.fragment) not in pages[path]:
                errors.append(f'Missing compatibility anchor: {url}')
    for relative, expected in assets['files'].items():
        if not expected['immutable']:
            continue
        path = output / relative
        if not path.is_file():
            errors.append(f'Missing immutable download: {base}{relative}')
        elif fingerprint(path) != {key: expected[key] for key in ('sha256', 'bytes')}:
            errors.append(f'Changed immutable download: {base}{relative}')
    current = source_files(root)
    for relative in sorted(assets['source_files'].keys() | current.keys()):
        if current.get(relative) != assets['source_files'].get(relative):
            errors.append(f'Changed benchmark source: {relative}')
    for source, anchors in routes['source_documents'].items():
        path = root / source
        if not path.is_file():
            errors.append(f'Missing source compatibility document: {source}')
            continue
        present = parse_document(path.read_text(encoding='utf-8')).anchors
        for anchor in sorted(set(anchors) - present):
            errors.append(f'Missing source compatibility anchor: {source}#{anchor}')
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist/site')
    parser.add_argument('--baseline-dir', type=Path, default=ROOT / 'website')
    parser.add_argument('--capture', action='store_true', help='Create a baseline; never overwrite one.')
    args = parser.parse_args()
    if args.capture:
        capture(args.output, args.baseline_dir)
        return 0
    routes = json.loads((args.baseline_dir / 'legacy-routes.json').read_text(encoding='utf-8'))
    assets = json.loads((args.baseline_dir / 'legacy-assets.json').read_text(encoding='utf-8'))
    errors = check(args.output, routes, assets)
    for error in errors:
        print(error)
    if errors:
        return 1
    count = sum(record['immutable'] for record in assets['files'].values())
    print(f'Compatibility passed: {len(routes["targets"])} URL mappings, {count} immutable downloads, '
          f'{len(assets["source_files"])} benchmark source files and source document anchors.')
    print('Browser redirects require the browser acceptance check; this command validates their targets.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
