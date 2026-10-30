#!/usr/bin/env python3
"""Check the built Next site, retained URLs and benchmark presentation data."""
from html.parser import HTMLParser
import json
import argparse
from pathlib import Path

from build_site import OUTPUT, SITE
from check_site import check as check_links
from check_website_compat import check as check_compatibility
from website_data import load_benchmark


class BenchmarkRows(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.rows = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'tr' and 'data-id' in attrs:
            self.rows[attrs['data-id']] = attrs


def check(output=OUTPUT):
    check_links(output, search_index=None)
    routes = json.loads((SITE / 'legacy-routes.json').read_text(encoding='utf-8'))
    assets = json.loads((SITE / 'legacy-assets.json').read_text(encoding='utf-8'))
    errors = check_compatibility(output, routes, assets)
    if errors:
        raise ValueError('\n'.join(errors))
    search = json.loads((output / 'search-index.json').read_text(encoding='utf-8'))
    if search.get('type') != 'advanced' or not (output / '_next/static').is_dir():
        raise ValueError('The static Fumadocs index or Next runtime is missing.')
    _, overview = load_benchmark()
    html = (output / 'benchmark/index.html').read_text(encoding='utf-8')
    rows = BenchmarkRows(html).rows
    if set(rows) != {model['id'] for model in overview['models']}:
        raise ValueError('The benchmark overview configuration set changed.')
    for model in overview['models']:
        row = rows[model['id']]
        for field in ('quality', 'successes', 'samples', 'p50', 'p95'):
            if float(row['data-' + field]) != model[field]:
                raise ValueError(f'Changed benchmark value: {model["id"]}/{field}')
        estimated = model['known_token_priced_usd'] is None and model.get('estimated_api_equivalent_usd') is not None
        cost = model.get('estimated_api_equivalent_usd') if estimated else model['known_token_priced_usd']
        actual_cost = None if row['data-cost'] == '' else float(row['data-cost'])
        if actual_cost != cost or row['data-cost-estimated'] != str(estimated).lower():
            raise ValueError(f'Changed benchmark cost semantics: {model["id"]}')
        if row['data-source'] != model['source']['label'] or row['data-channel'] != model['channel']:
            raise ValueError(f'Changed benchmark access source: {model["id"]}')
        if row['data-cost-partial'] != str(model.get('unknown_usage_calls', 0) > 0).lower():
            raise ValueError(f'Changed partial cost flag: {model["id"]}')
    home = (output / 'index.html').read_text(encoding='utf-8')
    for name in ('zotero-pdf2zh-pro.xpi', 'zotero-pdf2zh-pro-windows-x64-setup.exe'):
        if '/releases/latest/download/' + name not in home:
            raise ValueError(f'Missing stable download link: {name}')
    if 'plotly' in home.lower():
        raise ValueError('The home page must not load the 3D chart runtime.')
    print(f'Next compatibility passed: {len(routes["targets"])} URL mappings, '
          f'{sum(record["immutable"] for record in assets["files"].values())} immutable downloads, '
          f'{len(rows)} unchanged benchmark configurations and static Fumadocs search.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=OUTPUT)
    check(parser.parse_args().output)
