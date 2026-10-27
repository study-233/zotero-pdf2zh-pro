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
from pygments import highlight
from pygments.filter import Filter
from pygments.formatters import HtmlFormatter
from pygments.lexers import TextLexer, get_lexer_by_name
from pygments.token import Name, Text
from pygments.util import ClassNotFound

from check_docs import heading_slug

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / 'website'
OUTPUT = ROOT / 'dist/site'
BENCHMARK = ROOT / 'benchmarks/translation/site'
PUBLICATION = ROOT / 'benchmarks/translation/publications/2026-10-26-v2'
PILOT_PUBLICATION = ROOT / 'benchmarks/translation/publications/2026-10-26-interface-pilot-v1'
REGISTRY = ROOT / 'benchmarks/translation/publications/2026-10-27-unified-v1'
EXPERIMENT_PUBLICATION = ROOT / 'benchmarks/translation/publications/2026-10-27-interface-formal-v1'
REPO = 'https://github.com/study-233/zotero-pdf2zh-pro'
ORIGIN = 'https://study-233.github.io'
DEFAULT_BASE = '/zotero-pdf2zh-pro/'
GUIDE = 'docs/user-guide.md'
ANCHOR = re.compile(r'^<a id="([\w-]+)"></a>\s*$', re.M)
ATTRIBUTE = re.compile(r'\b(href|src)=([\"\x27])(.*?)\2')


class ShellCommands(Filter):
    """Give external commands and flags a role when Bash leaves them as plain text."""
    def filter(self, lexer, stream):
        line_start = True
        for kind, value in stream:
            if kind is Text:
                if line_start and re.fullmatch(r'[A-Za-z_][\w./+-]*', value):
                    kind = Name.Function
                elif re.fullmatch(r'--?[A-Za-z][\w-]*', value):
                    kind = Name.Attribute
            yield kind, value
            if '\n' in value:
                line_start = not value.rsplit('\n', 1)[1].strip()
            elif value.strip():
                line_start = False


def render_code_block(tokens, index, options, env):
    token = tokens[index]
    language = token.info.split()[0] if token.info.strip() else 'text'
    try:
        lexer = get_lexer_by_name(language, stripnl=False, ensurenl=False)
        label = '纯文本' if isinstance(lexer, TextLexer) else lexer.name
    except ClassNotFound:
        lexer, label = TextLexer(stripnl=False, ensurenl=False), language
    if 'bash' in lexer.aliases:
        lexer.add_filter(ShellCommands())
    code = highlight(token.content, lexer, HtmlFormatter(nowrap=True, classprefix='tok-'))
    return (f'<div class="code-block"><div class="code-toolbar">'
            f'<span class="code-language">{escape(label)}</span></div>'
            f'<pre><code class="language-{escape(language, quote=True)}">{code}</code></pre></div>\n')


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
        self.md.renderer.rules['fence'] = render_code_block
        self.screenshots = json.loads((SITE / 'screenshots.json').read_text(encoding='utf-8'))
        self.env = Environment(loader=FileSystemLoader(SITE / 'templates'),
                               autoescape=select_autoescape(['html']))
        self.pages = json.loads((SITE / 'routes.json').read_text(encoding='utf-8'))
        self.anchors = {}
        self.documents = {GUIDE: 'guide/', 'README.md': '', 'CHANGELOG.md': 'changelog/'}
        # Template images must be copied even when the guide no longer uses them.
        self.assets = {'assets/logo.svg', 'assets/task-manager.png',
                       'assets/guide/pdf-bilingual.png', 'assets/guide/selection-word.png'}
        self.assets.update(self.screenshots)
        self.assets.update(image['preview'] for image in self.screenshots.values() if 'preview' in image)
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
        toc, first, intro = [], True, True
        for i, token in enumerate(tokens):
            for child in token.children or []:
                if child.type != 'image':
                    continue
                path = posixpath.normpath(posixpath.join(posixpath.dirname(source), child.attrGet('src')))
                if image := self.screenshots.get(path):
                    child.attrSet('width', str(image['width']))
                    child.attrSet('height', str(image['height']))
                    child.attrSet('loading', 'lazy')
                    child.attrSet('decoding', 'async')
                    if 'preview' in image:
                        child.attrSet('srcset', f"{self.base}{image['preview']} {image['previewWidth']}w, {self.base}{path} {image['width']}w")
                        child.attrSet('sizes', '(min-width: 1700px) 1106px, (min-width: 1440px) 946px, (min-width: 901px) 75vw, 100vw')
            if token.type in ('html_block', 'inline'):
                for anchor in re.findall(r'<a id="([^"]+)"', token.content):
                    self.anchors[source, anchor] = route + '#' + anchor
            if not first and intro and token.type == 'paragraph_open':
                # Balance the opening summary separately from long reading paragraphs.
                if PlainText.read(self.md.renderInline(tokens[i + 1].content)):
                    token.attrSet('class', 'doc-intro')
                    intro = False
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

        benchmark = json.loads((REGISTRY / 'summary.json').read_text(encoding='utf-8'))
        if benchmark.get('experiment'):
            experiment = json.loads((EXPERIMENT_PUBLICATION / 'summary.json').read_text(encoding='utf-8'))
            if experiment.get('status') != 'audited_formal':
                raise ValueError('Only an adjudicated and audited experiment may appear on the comparison page')
            shutil.copytree(EXPERIMENT_PUBLICATION, output / 'benchmark/experiment', dirs_exist_ok=True)
        pilot = None
        if (PILOT_PUBLICATION / 'summary.json').exists():
            pilot = json.loads((PILOT_PUBLICATION / 'summary.json').read_text(encoding='utf-8'))
            if pilot.get('status') != 'audited_pilot':
                raise ValueError('Only an audited pilot may appear on the comparison page')
            shutil.copytree(PILOT_PUBLICATION, output / 'benchmark/pilot', dirs_exist_ok=True)
        write('home.html', title='在 Zotero 中读懂整篇论文', route='', kind='home',
              description='zotero-pdf2zh-pro 官方网站：整篇论文翻译、双语 PDF、划词精读，安装与使用图文教程。',
              models=benchmark['published_scope']['models'], papers=benchmark['method']['papers'])
        for i, page in enumerate(self.pages):
            content = self.render(page['tokens'], page['source'])
            self.index_page(page, content)
            next_page = (next(p for p in self.pages if p.get('anchor') == 'installation')
                         if page.get('anchor') in ('windows', 'macos', 'uv', 'advanced') else
                         self.pages[i + 1] if i + 1 < len(self.pages) else None)
            # OS installation chapters are alternatives, rather than sequential steps.
            previous_page = (self.pages[0] if page.get('anchor') in ('windows', 'macos', 'uv', 'advanced', 'installation')
                             else self.pages[i - 1] if i else None)
            write('guide.html', title=page['title'], route=page['route'], kind='guide',
                  description=page['title'] + '：zotero-pdf2zh-pro 图文使用教程。',
                  content=content, group=page['group'], source=page['source'], anchor=page.get('anchor'),
                  toc=page['toc'], previous=previous_page,
                  next=next_page)
        changes = self.render(self.changelog, 'CHANGELOG.md')
        changes = re.sub(r'(<h2[^>]*>v(\d+\.\d+\.\d+)[^<]*</h2>)',
                         lambda m: m[1] + f'<a class="release-download" href="{REPO}/releases/tag/v{m[2]}">下载此版本 ↗</a>', changes)
        write('changelog.html', title='更新记录', route='changelog/', kind='changelog', content=changes,
              description='zotero-pdf2zh-pro 的版本变化、下载入口与更新方法。')
        shutil.copytree(PUBLICATION, output / 'benchmark/data', dirs_exist_ok=True)
        shutil.copytree(PUBLICATION, output / 'benchmark/data/formal', dirs_exist_ok=True)
        shutil.copytree(REGISTRY, output / 'benchmark/data', dirs_exist_ok=True)
        # Keep one audited formal configuration per model and both measured GPT
        # access sources. The complete registry remains available for download.
        overview_ids = [
            'z-ai/glm-5.3-flash', '20261027/command-responses-none',
            'claude-haiku-5-5', '20261027/deepseek-chat-low',
            'xiaomi/mimo-v2.6-flash', 'Qwen/Qwen3.7-Flash',
            'minimax/minimax-m3.1-flash', 'Qwen/Qwen3.8-Flash',
            '20261027/mimo-responses-low', '20261027/account-responses-low',
        ]
        records = {model['id']: model for model in benchmark['models']}
        brand_names = {
            'z-ai/glm-5.3-flash': 'zhipu', 'gpt-6-luna': 'openai',
            'claude-haiku-5-5': 'anthropic', 'deepseek/deepseek-v4.1-flash': 'deepseek',
            'xiaomi/mimo-v2.6-flash': 'xiaomi', 'xiaomi/mimo-v2.6-pro': 'xiaomi',
            'Qwen/Qwen3.7-Flash': 'alibaba', 'Qwen/Qwen3.8-Flash': 'alibaba',
            'minimax/minimax-m3.1-flash': 'minimax',
        }
        channel_sources = {
            'Command Code': {'label': 'Command Code 套餐', 'icon': 'channels/command-code.svg'},
            'ChatGPT subscription': {'label': '插件 GPT 账号', 'icon': 'brands/openai.svg'},
        }
        overview = {**benchmark, 'models': [
            {**records[key], 'name': records[key]['model_name'] +
             (' · 插件账号' if records[key]['channel'] == 'ChatGPT subscription' else ' · Command Code')
             if records[key]['model_id'] == 'gpt-6-luna' else records[key]['model_name'],
             'chart_color': index,
             'chart_logo': brand_names[records[key]['model_id']],
             'source': channel_sources[records[key]['channel']]}
            for index, key in enumerate(overview_ids)
        ]}
        if any(model['kind'] != 'formal' or model['samples'] != 240 for model in overview['models']):
            raise ValueError('The model overview requires 240-sample formal results')
        overview['date_label'] = '2026-10-23 — 27'
        overview['model_count'] = len({model['model_id'] for model in overview['models']})
        overview['sources'] = [channel_sources[channel] for channel in
                               dict.fromkeys(model['channel'] for model in overview['models'])]
        write('benchmark.html', title='翻译模型对比', route='benchmark/', kind='benchmark',
              description='翻译模型实测：直观比较翻译质量、速度与费用，查看三维性价比和原始测评数据。',
              benchmark=overview, ui_icons={
                  path.stem: path.read_text(encoding='utf-8')
                  for path in (SITE / 'static/vendor/pricing-ui').glob('*.svg')
              })
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
