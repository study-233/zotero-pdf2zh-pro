#!/usr/bin/env python3
"""Build the public website from repository documentation and frozen benchmark data."""
from __future__ import annotations

import argparse
from collections import Counter
from html import escape, unescape
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import posixpath
import re
import shutil
from urllib.parse import quote, unquote, urlsplit

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markdown_it import MarkdownIt

from check_docs import heading_slug

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / 'website'
OUTPUT = ROOT / 'dist/site'
BENCHMARK = ROOT / 'benchmarks/translation/site'
REPO = 'https://github.com/study-233/zotero-pdf2zh-pro'
ORIGIN = 'https://study-233.github.io'
DEFAULT_BASE = '/zotero-pdf2zh-pro/'
GUIDE = 'docs/user-guide.md'
ANCHOR = re.compile(r'^<a id="([\w-]+)"></a>\s*$', re.M)
ATTRIBUTE = re.compile(r'\b(href|src)=([\"\x27])(.*?)\2')


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, value):
        self.parts.append(value)

    @classmethod
    def read(cls, html):
        parser = cls()
        parser.feed(html)
        return re.sub(r'\s+', ' ', ' '.join(parser.parts)).strip()


def guide_sections(text):
    """Explicit anchors are the stable chapter boundaries of the user guide."""
    matches = list(ANCHOR.finditer(text))
    if len({m[1] for m in matches}) != len(matches):
        raise ValueError('Duplicate guide chapter anchor')
    return {m[1]: text[m.start():matches[i + 1].start() if i + 1 < len(matches) else len(text)]
            for i, m in enumerate(matches)}


class Website:
    def __init__(self, base=DEFAULT_BASE):
        if not re.fullmatch(r'/(?:[A-Za-z0-9_-]+/)*', base):
            raise ValueError('Base must be an absolute directory URL, e.g. /zotero-pdf2zh-pro/')
        self.base = base
        self.md = MarkdownIt('commonmark', {'html': True}).enable(['table', 'strikethrough'])
        self.env = Environment(loader=FileSystemLoader(SITE / 'templates'),
                               autoescape=select_autoescape(['html']))
        self.pages = json.loads((SITE / 'routes.json').read_text(encoding='utf-8'))
        self.anchors = {}
        self.documents = {GUIDE: 'guide/', 'README.md': '', 'CHANGELOG.md': 'changelog/'}
        # Template images must be copied even when the guide no longer uses them.
        self.assets = {'assets/logo.svg', 'assets/task-manager.png',
                       'assets/guide/pdf-bilingual.png', 'assets/guide/selection-word.png'}
        self.search = []
        self.version = json.loads((ROOT / 'plugin/package.json').read_text(encoding='utf-8'))['version']
        sections = guide_sections((ROOT / GUIDE).read_text(encoding='utf-8'))
        configured = [p['anchor'] for p in self.pages if 'anchor' in p]
        if set(configured) != sections.keys() or len(configured) != len(set(configured)):
            raise ValueError('Every guide chapter must appear exactly once in website/routes.json')
        counts = {}
        for page in self.pages:
            page['source'] = page.get('source', GUIDE)
            page['route'] = 'guide/' + (page['slug'] + '/' if page['slug'] else '')
            self.documents.setdefault(page['source'], page['route'])
            body = sections[page['anchor']] if 'anchor' in page else (ROOT / page['source']).read_text(encoding='utf-8')
            page['tokens'], page['toc'] = self.parse(body, page['source'], page['route'], counts)
        self.changelog, _ = self.parse((ROOT / 'CHANGELOG.md').read_text(encoding='utf-8'),
                                      'CHANGELOG.md', 'changelog/', counts)
        for anchor in ('features', 'quick-start', 'windows', 'macos', 'community'):
            self.anchors['README.md', anchor] = '#' + anchor
        for page in self.pages:
            if 'anchor' in page and ('README.md', page['anchor']) not in self.anchors:
                self.anchors['README.md', page['anchor']] = page['route'] + '#' + page['anchor']

    def parse(self, text, source, route, counts):
        labels = {'TIP': '提示', 'NOTE': '说明', 'IMPORTANT': '重要', 'WARNING': '注意', 'CAUTION': '注意'}
        text = re.sub(r'^> \[!(TIP|NOTE|IMPORTANT|WARNING|CAUTION)\]\s*$',
                      lambda m: '> **' + labels[m[1]] + '**\n>', text, flags=re.M)
        tokens = self.md.parse(text)
        used = counts.setdefault(source, Counter())
        toc, first = [], True
        for i, token in enumerate(tokens):
            if token.type in ('html_block', 'inline'):
                for anchor in re.findall(r'<a id="([^"]+)"', token.content):
                    self.anchors[source, anchor] = route + '#' + anchor
            if token.type != 'heading_open':
                continue
            title = tokens[i + 1].content
            slug = heading_slug(title)
            suffix = used[slug]
            used[slug] += 1
            slug += '-' + str(suffix) if suffix else ''
            self.anchors[source, slug] = route + '#' + slug
            token.attrSet('id', slug)
            if first:
                # The template supplies the page title; preserve the source title's URL.
                token.type, token.tag, token.nesting = 'html_block', '', 0
                token.content = '<span id="' + escape(slug) + '"></span>\n'
                tokens[i + 1].content = ''
                tokens[i + 1].children = []
                tokens[i + 2].hidden = True
                first = False
            else:
                level = max(2, int(token.tag[1:]) - (1 if source == GUIDE else 0))
                token.tag = tokens[i + 2].tag = 'h' + str(level)
                toc.append({'id': slug, 'title': PlainText.read(self.md.renderInline(title))})
        return tokens, toc

    def destination(self, url, source):
        parts = urlsplit(unescape(url))
        if parts.scheme or parts.netloc or url.startswith('/'):
            return url
        path = posixpath.normpath(posixpath.join(posixpath.dirname(source), unquote(parts.path))) if parts.path else source
        fragment = unquote(parts.fragment)
        if fragment and (path, fragment) in self.anchors:
            return self.base + self.anchors[path, fragment]
        if path in self.documents:
            if fragment:
                raise ValueError(f'Unmapped documentation anchor: {source} -> {url}')
            return self.base + self.documents[path]
        if path.startswith(('assets/', 'docs/examples/')) and (ROOT / path).is_file():
            self.assets.add(path)
            return self.base + quote(path)
        if not (ROOT / path).is_file():
            raise ValueError(f'Missing linked source: {source} -> {url}')
        return REPO + '/blob/main/' + quote(path) + ('#' + quote(fragment) if fragment else '')

    def render(self, tokens, source):
        html = self.md.renderer.render(tokens, self.md.options, {})
        return ATTRIBUTE.sub(lambda m: m[1] + '="' + escape(self.destination(m[3], source), quote=True) + '"', html)

    def context(self, **values):
        return dict(base=self.base, origin=ORIGIN, repo=REPO, release=REPO + '/releases/latest',
                    version=self.version, pages=self.pages, asset=self.asset_url,
                    groups=list(dict.fromkeys(p['group'] for p in self.pages)), **values)

    def asset_url(self, path):
        source = SITE / 'static' / path.removeprefix('site/') if path.startswith('site/') else BENCHMARK / path
        digest = hashlib.sha256(source.read_bytes()).hexdigest()[:12]
        return self.base + path + '?v=' + digest

    def index_page(self, page, content):
        chunks = re.split(r'(<h[2-6]\b[^>]*>.*?</h[2-6]>)', content, flags=re.S)
        section, url = page['title'], self.base + page['route']
        for chunk in chunks:
            heading = re.match(r'<h[2-6]\b[^>]*\bid="([^"]+)"[^>]*>(.*?)</h[2-6]>', chunk, re.S)
            if heading:
                section, url = PlainText.read(heading[2]), self.base + page['route'] + '#' + heading[1]
            else:
                text = PlainText.read(chunk)
                if text:
                    self.search.append(dict(title=page['title'], section=section, url=url, text=text))

    def build(self, output):
        output.mkdir(parents=True, exist_ok=True)
        # The historical root data/PDF URLs remain byte-for-byte identical.
        shutil.copytree(BENCHMARK, output, dirs_exist_ok=True)
        shutil.copytree(SITE / 'static', output / 'site', dirs_exist_ok=True)
        urls = []

        def write(template, **values):
            route = values['route']
            path = output / route / 'index.html'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(self.env.get_template(template).render(**self.context(**values)), encoding='utf-8')
            urls.append(ORIGIN + self.base + route)

        benchmark = json.loads((BENCHMARK / 'results.json').read_text(encoding='utf-8'))
        write('home.html', title='在 Zotero 中读懂整篇论文', route='', kind='home',
              description='zotero-pdf2zh-pro 官方网站：整篇论文翻译、双语 PDF、划词精读，安装与使用图文教程。',
              models=len(benchmark['models']), papers=len(benchmark['papers']))
        for i, page in enumerate(self.pages):
            content = self.render(page['tokens'], page['source'])
            self.index_page(page, content)
            next_page = (next(p for p in self.pages if p.get('anchor') == 'installation')
                         if page.get('anchor') in ('windows', 'macos') else
                         self.pages[i + 1] if i + 1 < len(self.pages) else None)
            write('guide.html', title=page['title'], route=page['route'], kind='guide',
                  description=page['title'] + '：zotero-pdf2zh-pro 图文使用教程。',
                  content=content, group=page['group'], source=page['source'], anchor=page.get('anchor'),
                  toc=page['toc'], previous=self.pages[i - 1] if i else None,
                  next=next_page)
        changes = self.render(self.changelog, 'CHANGELOG.md')
        changes = re.sub(r'(<h2[^>]*>v(\d+\.\d+\.\d+)[^<]*</h2>)',
                         lambda m: m[1] + f'<a class="release-download" href="{REPO}/releases/tag/v{m[2]}">下载此版本 ↗</a>', changes)
        write('changelog.html', title='更新记录', route='changelog/', kind='changelog', content=changes,
              description='zotero-pdf2zh-pro 的版本变化、下载入口与更新方法。')
        original = (BENCHMARK / 'index.html').read_text(encoding='utf-8')
        content = re.search(r'<main>(.*?)</main>', original, re.S)[1]
        dialogs = re.search(r'(<dialog\b.*?</dialog>)', original, re.S)[1]
        # Asset links in the original benchmark are relative to the historical root.
        def benchmark_link(m):
            url = m[3]
            return m[0] if url.startswith(('#', 'https:', 'http:')) else m[1] + '="' + self.base + url + '"'
        content = ATTRIBUTE.sub(benchmark_link, content)
        write('benchmark.html', title='翻译模型实测', route='benchmark/', kind='benchmark',
              description='真实 PDF 翻译的质量、费用和耗时对比，附双语 PDF 与匿名评审记录。',
              content=content, dialogs=dialogs)
        for asset in self.assets:
            target = output / asset
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / asset, target)
        (output / 'search-index.json').write_text(json.dumps(self.search, ensure_ascii=False), encoding='utf-8')
        (output / '.nojekyll').touch()
        (output / 'sitemap.xml').write_text('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' +
            ''.join('<url><loc>' + escape(url) + '</loc></url>' for url in urls) + '</urlset>', encoding='utf-8')
        print(f'Built {len(urls)} pages and {len(self.search)} searchable sections in {output}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', default=DEFAULT_BASE)
    args = parser.parse_args()
    site = Website(args.base)
    # This command only replaces its dedicated, ignored build directory.
    expected = ROOT.resolve() / 'dist' / 'site'
    if OUTPUT.resolve() != expected or OUTPUT.is_symlink():
        raise ValueError('Refusing to replace an output directory outside dist/site')
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    site.build(OUTPUT)


if __name__ == '__main__':
    main()
