#!/usr/bin/env python3
"""Read-only, offline checks for the project's documented Markdown subset."""

import ast
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import tomllib
import unicodedata
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = (
    'README.md', 'docs/user-guide.md', 'docs/readme-maintenance.md',
    'docs/development-notes.md', 'CLAUDE.md',
)
PUBLIC_ANCHORS = frozenset('''features quick-start windows macos installation
api-configuration usage translation-options task-management maintenance
troubleshooting advanced community'''.split())
MARKER = '<!-- release-version -->'
VERSION_PATTERN = re.compile(r'<!-- release-version --> `([^`\n]+)`')


def blank(text):
    """Hide examples while preserving offsets and line numbers."""
    return re.sub(r'[^\n]', ' ', text)


def prose(text):
    text = re.sub(r'<!--[\s\S]*?-->', lambda m: blank(m[0]), text)
    lines = text.splitlines(keepends=True)
    fence = None
    for i, line in enumerate(lines):
        opening = re.match(r'^ {0,3}(`{3,}|~{3,})', line)
        if fence:
            if re.match(r'^ {0,3}' + re.escape(fence[0]) +
                        '{' + str(len(fence)) + r',}\s*$', line):
                fence = None
            lines[i] = blank(line)
        elif opening:
            fence = opening[1]
            lines[i] = blank(line)
    return ''.join(lines)


def without_inline_code(text):
    return re.sub(r'(`+)(?!`)([\s\S]*?)(?<!`)\1(?!`)',
                  lambda m: blank(m[0]), text)


def heading_slug(text):
    text = re.sub(r'!?(\[([^\]]+)\])\([^)]*\)', r'\2', text)
    text = unescape(re.sub(r'<[^>]*>', '', text)).lower()
    text = ''.join(c for c in text if c in '-_ ' or
                   unicodedata.category(c)[0] in 'LNM')
    return text.replace(' ', '-')


@dataclass
class Document:
    anchors: set = field(default_factory=set)
    explicit: dict = field(default_factory=dict)
    links: list = field(default_factory=list)
    errors: list = field(default_factory=list)


class HTMLReferences(HTMLParser):
    def __init__(self, doc):
        super().__init__(convert_charrefs=True)
        self.doc = doc

    def handle_starttag(self, tag, attrs):
        line = self.getpos()[0]
        for key, value in attrs:
            if value is None:
                continue
            if key == 'id' or (tag == 'a' and key == 'name'):
                if value in self.doc.explicit:
                    self.doc.errors.append((line, f'duplicate explicit anchor: {value}'))
                self.doc.explicit[value] = line
                self.doc.anchors.add(value)
            elif key in ('href', 'src'):
                self.doc.links.append((line, value))

    handle_startendtag = handle_starttag


def destination(text, start):
    """Read a Markdown destination, including nested/escaped parentheses."""
    i = start
    while i < len(text) and text[i].isspace():
        i += 1
    if i < len(text) and text[i] == '<':
        end = text.find('>', i + 1)
        return text[i + 1:end] if end != -1 else ''
    begin, depth = i, 0
    while i < len(text):
        c = text[i]
        if c == '\\' and i + 1 < len(text):
            i += 2
            continue
        if c == '(':
            depth += 1
        elif c == ')':
            if depth == 0:
                break
            depth -= 1
        elif c.isspace() and depth == 0:
            break
        i += 1
    return re.sub(r'\\([()])', r'\1', text[begin:i])


def parse_document(text):
    doc = Document()
    body = prose(text)
    used = set()
    for line, content in enumerate(body.splitlines(), 1):
        match = re.match(r'^ {0,3}#{1,6}\s+(.+?)\s*$', content)
        if match:
            slug = heading_slug(re.sub(r'\s+#+\s*$', '', match[1]))
            candidate, suffix = slug, 0
            while candidate in used:
                suffix += 1
                candidate = f'{slug}-{suffix}'
            used.add(candidate)
            doc.anchors.add(candidate)
    links_body = without_inline_code(body)
    HTMLReferences(doc).feed(links_body)
    for match in re.finditer(r'(?<!\\)\]\(', links_body):
        doc.links.append((links_body.count('\n', 0, match.start()) + 1,
                          destination(links_body, match.end())))
    # Full, collapsed and defined shortcut references; ignore task checkboxes.
    normalize = lambda label: ' '.join(label.split()).casefold()
    definitions = {}
    definition_lines = set()
    for line, content in enumerate(links_body.splitlines(), 1):
        match = re.match(r'^ {0,3}\[([^\]]+)\]:\s*(.*)$', content)
        if match:
            key = normalize(match[1])
            url = destination(match[2], 0)
            definitions[key] = url
            definition_lines.add(line)
            doc.links.append((line, url))
    for match in re.finditer(r'(?<![\\\[])\[([^\[\]\n]+)\](?:\[([^\]\n]*)\])?', links_body):
        line = links_body.count('\n', 0, match.start()) + 1
        if line in definition_lines or links_body[match.end():match.end() + 1] == '(':
            continue
        label = normalize(match[2] or match[1])
        if match[2] is not None and label not in definitions:
            doc.errors.append((line, f'undefined link reference: {label}'))
        elif label in definitions:
            doc.links.append((line, definitions[label]))
    return doc


def check_links(root, paths):
    errors, cache = [], {}

    def load(path):
        if path not in cache:
            doc = parse_document(path.read_text(encoding='utf-8'))
            cache[path] = doc
            errors.extend(f'{path.relative_to(root)}:{line}: {message}'
                          for line, message in doc.errors)
        return cache[path]

    for name in paths:
        path = root / name
        if not path.is_file():
            errors.append(f'{name}:1: document does not exist')
            continue
        doc = load(path)
        for line, url in doc.links:
            try:
                parts = urlsplit(unescape(url))
            except ValueError as error:
                errors.append(f'{name}:{line}: invalid URL {url!r}: {error}')
                continue
            if parts.scheme or parts.netloc:
                continue
            target = ((root if parts.path.startswith('/') else path.parent) /
                      unquote(parts.path).lstrip('/')).resolve() if parts.path else path
            if not target.is_relative_to(root):
                errors.append(f'{name}:{line}: link escapes repository: {url}')
            elif not target.exists():
                errors.append(f'{name}:{line}: missing local target: {url}')
            elif parts.fragment and target.suffix.lower() == '.md':
                if unquote(parts.fragment) not in load(target).anchors:
                    errors.append(f'{name}:{line}: missing anchor: {url}')
    return errors


def check_readme(root):
    errors, warnings = [], []
    path = root / 'README.md'
    if not path.is_file():
        return ['README.md:1: document does not exist'], warnings
    text = path.read_text(encoding='utf-8')
    doc = parse_document(text)
    for anchor in sorted(PUBLIC_ANCHORS - doc.explicit.keys()):
        errors.append(f'README.md:1: missing public anchor: {anchor}')
    matches = list(VERSION_PATTERN.finditer(text))
    if text.count(MARKER) != 1 or len(matches) != 1:
        errors.append('README.md:1: expected one release version marker followed by `version`')
    else:
        marker_line = text.count('\n', 0, matches[0].start()) + 1
        versions = {}
        sources = ('plugin/package.json', 'server/pyproject.toml', 'server/server.py')
        for source in sources:
            try:
                content = (root / source).read_text(encoding='utf-8')
                if source.endswith('.json'):
                    version = json.loads(content)['version']
                elif source.endswith('.toml'):
                    version = tomllib.loads(content)['project']['version']
                else:
                    values = [ast.literal_eval(node.value) for node in ast.parse(content).body
                              if isinstance(node, ast.Assign) and
                              any(isinstance(t, ast.Name) and t.id == 'VERSION' for t in node.targets)]
                    if len(values) != 1:
                        raise ValueError('expected a single VERSION assignment')
                    version = values[0]
                versions[source] = version
            except (OSError, ValueError, KeyError, SyntaxError) as error:
                errors.append(f'{source}:1: cannot read version: {error}')
        for source, version in versions.items():
            if version != matches[0][1]:
                errors.append(f'README.md:{marker_line}: version {matches[0][1]} differs from {source}: {version}')
    length = len(text.splitlines())
    if length > 280:
        warnings.append(f'README.md:281: {length} lines; consider moving details into the user guide')
    return errors, warnings


def main():
    errors = check_links(ROOT, DOCUMENTS)
    readme_errors, warnings = check_readme(ROOT)
    errors.extend(readme_errors)
    for message in warnings:
        print(f'WARNING {message}')
    for message in errors:
        print(f'ERROR {message}')
    if errors:
        print(f'Documentation checks failed: {len(errors)} error(s).')
        return 1
    print(f'Documentation checks passed ({len(DOCUMENTS)} entry documents, local links and release version).')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
