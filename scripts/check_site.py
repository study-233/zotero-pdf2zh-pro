#!/usr/bin/env python3
"""Check generated HTML links, anchors, assets and search destinations offline."""
from html.parser import HTMLParser
from pathlib import Path
import json
import re
from urllib.parse import unquote, urljoin, urlsplit

from build_site import DEFAULT_BASE, OUTPUT


class Page(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.ids, self.links, self.errors = set(), [], []
        self.headings = 0
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'h1':
            self.headings += 1
        if 'id' in attrs:
            if attrs['id'] in self.ids:
                self.errors.append('Duplicate ID: ' + attrs['id'])
            self.ids.add(attrs['id'])
        for key in ('href', 'src'):
            if attrs.get(key):
                self.links.append(attrs[key])
        if tag == 'img' and 'alt' not in attrs:
            self.errors.append('Image has no alt text')


def check(output=OUTPUT, base=DEFAULT_BASE):
    pages = {path: Page(path.read_text(encoding='utf-8')) for path in output.rglob('*.html')}
    errors = []

    def link_target(url, source_url, label):
        parsed = urlsplit(url)
        if parsed.scheme or parsed.netloc:
            return
        resolved = urlsplit(urljoin(source_url, url))
        if not resolved.path.startswith(base):
            errors.append(f'{label}: URL escaped project base: {url}')
            return
        path = output / unquote(resolved.path[len(base):])
        if path.is_dir():
            path /= 'index.html'
        if not path.is_file():
            errors.append(f'{label}: Missing file: {url}')
        elif resolved.fragment and path in pages and unquote(resolved.fragment) not in pages[path].ids:
            errors.append(f'{label}: Missing anchor: {url}')

    for path, page in pages.items():
        label = path.relative_to(output).as_posix()
        errors.extend(f'{label}: {error}' for error in page.errors)
        if page.headings != 1:
            errors.append(f'{label}: Expected exactly one h1, found {page.headings}')
        source_url = base + label
        for url in page.links:
            link_target(url, source_url, label)
    for record in json.loads((output / 'search-index.json').read_text(encoding='utf-8')):
        link_target(record['url'], base, 'search-index.json')
        if not record['text'].strip():
            errors.append('Empty search record')
    for path in output.rglob('*.css'):
        for url in re.findall(r'url\([\"\x27]?([^\)\"\x27]+)', path.read_text(encoding='utf-8')):
            link_target(url, base + path.relative_to(output).as_posix(), path.name)
    if errors:
        raise ValueError('\n'.join(errors))
    print(f'Validated links, anchors and assets across {len(pages)} pages.')


if __name__ == '__main__':
    check()
