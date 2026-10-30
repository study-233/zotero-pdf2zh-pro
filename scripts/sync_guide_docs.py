#!/usr/bin/env python3
"""Generate GitHub-readable guides from the website's authored MDX chapters."""
import argparse
import json
from pathlib import Path
import posixpath
import re
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parents[1]
BASE = '/zotero-pdf2zh-pro/'
REPO = 'https://github.com/study-233/zotero-pdf2zh-pro/blob/main/'
SOURCE = Path('website/src/content/docs/guide')
GENERATED = '<!-- Generated from website/src/content/docs/guide/*.mdx. Edit those chapters, then run python scripts/sync_guide_docs.py. -->'


def read_chapter(path):
    text = path.read_text(encoding='utf-8')
    parts = text.split('---', 2)
    if len(parts) != 3 or parts[0].strip():
        raise ValueError(f'Missing frontmatter: {path}')
    # JSON is a YAML subset and keeps this compatibility exporter deterministic.
    metadata = json.loads(parts[1])
    return metadata, parts[2].strip() + '\n'


def compatibility_body(body, destination, routes):
    mapping = {p['slug'] or 'preparation': p for p in routes}

    def link(url):
        if url.startswith(REPO):
            parsed = urlsplit(url[len(REPO):])
            relative = posixpath.relpath(unquote(parsed.path), posixpath.dirname(destination))
            return relative + ('#' + unquote(parsed.fragment) if parsed.fragment else '')
        if not url.startswith(BASE):
            return url
        parsed = urlsplit(url[len(BASE):])
        if parsed.path.startswith('guide/'):
            slug = parsed.path.removeprefix('guide/').strip('/') or 'preparation'
            route = mapping[slug]
            target = route.get('source', 'docs/user-guide.md')
            fragment = unquote(parsed.fragment) or route.get('anchor', '')
            relative = '' if target == destination else posixpath.relpath(target, posixpath.dirname(destination))
            return relative + ('#' + fragment if fragment else '')
        return posixpath.relpath(unquote(parsed.path), posixpath.dirname(destination)) + ('#' + unquote(parsed.fragment) if parsed.fragment else '')

    lines = body.splitlines(keepends=True)
    excluded = set()
    for token in MarkdownIt().parse(body):
        if token.type in ('fence', 'code_block') and token.map:
            excluded.update(range(*token.map))
    for i, line in enumerate(lines):
        if i in excluded:
            continue
        chunks = re.split(r'(`+[^`\n]*`+)', line)
        for j in range(0, len(chunks), 2):
            chunks[j] = re.sub(r'(?<=\]\()([^\n]+?)(?=\))', lambda m: link(m[1]), chunks[j])
        line = ''.join(chunks)
        if destination == 'docs/user-guide.md':
            line = re.sub(r'^(#{2,5}) ', r'#\1 ', line)
        lines[i] = line
    return ''.join(lines)


def generated_documents(root=ROOT):
    routes = json.loads((root / 'website/routes.json').read_text(encoding='utf-8'))
    guide = ['# 使用指南', GENERATED, '[返回项目首页](../README.md)',
             '按 **本地服务 → Zotero 插件 → 翻译来源 → 第一篇 PDF** 完成首次使用。', '## 目录']
    guide += ['- [' + p['title'] + '](' + ('#' + p['anchor'] if 'anchor' in p else posixpath.relpath(p['source'], 'docs')) + ')' for p in routes]
    outputs = {}
    for page in routes:
        slug = page['slug'] or 'preparation'
        metadata, body = read_chapter(root / SOURCE / (slug + '.mdx'))
        if metadata['title'] != page['title']:
            raise ValueError(f'Guide title differs from routes.json: {slug}')
        destination = page.get('source', 'docs/user-guide.md')
        body = compatibility_body(body, destination, routes)
        if 'anchor' in page:
            anchor = page['anchor']
            body = body.replace(f'<span id="{anchor}"></span>\n', '').lstrip()
            guide.extend([f'<a id="{anchor}"></a>', '## ' + page['title'], body.rstrip()])
        else:
            outputs[destination] = '# ' + page['title'] + '\n\n' + GENERATED + '\n\n' + body
    outputs['docs/user-guide.md'] = '\n\n'.join(guide) + '\n'
    return outputs


def sync(root=ROOT, check=False):
    changed = []
    for relative, expected in generated_documents(root).items():
        path = root / relative
        actual = path.read_text(encoding='utf-8') if path.exists() else ''
        if actual != expected:
            changed.append(relative)
            if not check:
                path.write_text(expected, encoding='utf-8')
    if check and changed:
        raise ValueError('Generated guides are stale; run scripts/sync_guide_docs.py: ' + ', '.join(changed))
    return changed


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    changed = sync(check=args.check)
    print('Guide source sync passed.' if args.check else f'Updated {len(changed)} generated guide documents.')
