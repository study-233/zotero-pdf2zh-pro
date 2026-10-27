import hashlib
import csv
from html.parser import HTMLParser
import json
import re
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from build_site import BENCHMARK, DEFAULT_BASE, GUIDE, REGISTRY, ROOT, Website, guide_sections
from check_docs import parse_document
from check_site import Page, check


class CodeText(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.in_code, self.parts = False, []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == 'code':
            self.in_code = True

    def handle_endtag(self, tag):
        if tag == 'code':
            self.in_code = False

    def handle_data(self, value):
        if self.in_code:
            self.parts.append(value)


class WebsiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.output = Path(cls.temp.name)
        cls.site = Website()
        cls.site.build(cls.output)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_all_local_links_search_and_css_assets(self):
        check(self.output)

    def test_every_guide_anchor_resolves_after_splitting(self):
        original = parse_document((ROOT / GUIDE).read_text(encoding='utf-8'))
        for anchor in original.anchors - {'使用指南', '目录'}:
            with self.subTest(anchor=anchor):
                target = self.site.destination('#' + anchor, GUIDE)
                path, fragment = target.removeprefix(DEFAULT_BASE).split('#', 1)
                document = Page((self.output / path / 'index.html').read_text(encoding='utf-8'))
                self.assertIn(fragment, document.ids)

    def test_windows_and_macos_have_next_steps_and_stable_downloads(self):
        for platform in ('windows', 'macos'):
            html = (self.output / 'guide' / platform / 'index.html').read_text(encoding='utf-8')
            self.assertIn(DEFAULT_BASE + 'guide/installation/', html)
            self.assertNotIn('href="user-guide.md', html)
            self.assertRegex(html, r'下一篇 →</small><span>安装 Zotero 插件</span>')
        home = (self.output / 'index.html').read_text(encoding='utf-8')
        self.assertIn('/releases/latest/download/zotero-pdf2zh-pro.xpi', home)
        self.assertIn('/releases/latest/download/zotero-pdf2zh-pro-windows-x64-setup.exe', home)
        self.assertIn('本地服务与 Zotero 插件都需要安装', home)

    def test_public_benchmark_downloads_are_unchanged(self):
        for path in [BENCHMARK / name for name in ('results.json', 'results.csv', 'pdfs.json')] + list((BENCHMARK / 'pdfs').glob('*.pdf')):
            with self.subTest(path=path.name):
                output = self.output / path.relative_to(BENCHMARK)
                self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), hashlib.sha256(output.read_bytes()).digest())

    def test_theme_and_benchmark_scripts_are_shared_from_project_root(self):
        html = (self.output / 'benchmark/index.html').read_text(encoding='utf-8')
        for script in ('ui.js', 'site/benchmark.js', 'site/vendor/plotly/plotly-gl3d-4.1.2.min.js'):
            self.assertRegex(html, f'src="{re.escape(DEFAULT_BASE + script)}\\?v=[0-9a-f]{{12}}"')
        self.assertIn('href="data/comparison.csv"', html)
        self.assertIn('id="model-comparison"', html)
        for element in ('results', 'compare', 'method', 'benchmark-chart', 'tab-speed', 'tab-cost', 'tab-value'):
            self.assertIn(f'id="{element}"', html)
        for removed in ('history-benchmark', 'data-benchmark-set', 'pdf-viewer.js', 'app.js'):
            self.assertNotIn(removed, html)
        self.assertFalse((self.output / 'benchmark/v2/index.html').exists())
        with (self.output / 'benchmark/data/comparison.csv').open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.DictReader(stream))
        registry = json.loads((REGISTRY / 'summary.json').read_text(encoding='utf-8'))
        records = {model['id']: model for model in registry['models']}
        self.assertEqual(len(rows), len(records))
        self.assertEqual(sum(row['scope'] == 'formal' for row in rows), sum(model['kind'] == 'formal' for model in records.values()))
        self.assertEqual(sum(row['scope'] == 'pilot' for row in rows), sum(model['kind'] == 'pilot' for model in records.values()))
        glm = next(row for row in rows if row['model'] == 'GLM-5.3 Flash')
        self.assertEqual(glm['successful_requests'], '240')
        chatgpt = next(row for row in rows if 'ChatGPT' in row['model'])
        self.assertEqual(chatgpt['recorded_usage_usd'], '')
        self.assertEqual(float(chatgpt['estimated_api_equivalent_usd']), 0.0487976)
        for row in rows:
            model = records[row['record_id']]
            expected = model.get('estimated_api_equivalent_usd')
            self.assertEqual(row['estimated_api_equivalent_usd'], '' if expected is None else str(expected))
            if model['normalized_cost_estimated']:
                self.assertEqual(row['recorded_usage_usd'], '')
        # Keep the complete registry downloadable, while the chart stays a
        # readable nine-model overview, including both GPT access sources.
        chart_ids = re.findall(r'<tr data-id="([^"]+)"', html)
        self.assertEqual(len(chart_ids), 10)
        self.assertEqual(len({records[key]['model_id'] for key in chart_ids}), 9)
        self.assertIn('id="visible-model-count">10</strong>', html)
        chart_names = re.findall(r'<tr data-id="[^"]+" data-name="([^"]+)"', html)
        self.assertEqual(len(set(chart_names)), 10)
        for element in ('tab-table', 'copy-view', 'drag-controls'):
            self.assertIn(f'id="{element}"', html)
        self.assertIn('每组 240 条', html)
        self.assertIn('20261027/command-responses-none', chart_ids)
        self.assertIn('20261027/account-responses-low', chart_ids)
        self.assertIn('GPT-6 Luna · Command Code', chart_names)
        self.assertIn('GPT-6 Luna · 插件账号', chart_names)
        self.assertIn('data-cost="0.0490566" data-cost-estimated="true"', html)
        self.assertIn('≈$0.04906', html)
        self.assertIn('API 等值估算', html)
        self.assertIn('20261027/deepseek-chat-low', chart_ids)
        self.assertIn('20261027/mimo-responses-low', chart_ids)
        for key in chart_ids:
            self.assertEqual(records[key]['kind'], 'formal')
            self.assertEqual(records[key]['samples'], 240)
        # The visible access source must come from each measured configuration,
        # rather than being inferred from the model's manufacturer logo.
        source_rows = re.findall(
            r'<tr data-id="([^"]+)"[^>]*data-source="([^"]+)"[^>]*data-channel="([^"]+)"', html)
        self.assertEqual(len(source_rows), len(chart_ids))
        for key, label, channel in source_rows:
            self.assertEqual(channel, records[key]['channel'])
            self.assertEqual(label, 'Command Code 套餐' if channel == 'Command Code' else '插件 GPT 账号')
        self.assertIn('aria-label="当前总览接入来源"', html)
        self.assertIn('data-configuration="Responses · 关闭 · 2026-10-27"', html)
        self.assertIn('data-cost-partial="true"', html)
        self.assertIn('≥$', html)
        for row in rows:
            self.assertEqual(row['samples'], '240' if row['scope'] == 'formal' else '24')
        for element in ('filter-scope', 'fee-basis', 'record-selector', 'sample-state', 'pair-selector', 'pair-apply'):
            self.assertNotIn(f'id="{element}"', html)
        self.assertNotIn('<select', html)
        self.assertNotIn('class="site-header"', html)
        self.assertNotIn(DEFAULT_BASE + 'site/site.js', html)
        with (self.output / 'benchmark/data/sample-scores.csv').open(encoding='utf-8-sig', newline='') as stream:
            self.assertEqual(len(list(csv.DictReader(stream))), registry['sample_score_rows'])
        with (self.output / 'benchmark/data/calls.csv').open(encoding='utf-8-sig', newline='') as stream:
            self.assertEqual(len(list(csv.DictReader(stream))), registry['ledger_rows'])
        for name in ('summary.json', 'comparison.csv', 'sample-scores.csv', 'calls.csv'):
            self.assertEqual((REGISTRY / name).read_bytes(), (self.output / 'benchmark/data' / name).read_bytes())
        for source, destination in [('2026-10-26-v2', 'data/formal'), ('2026-10-26-interface-pilot-v1', 'pilot')]:
            original = ROOT / 'benchmarks/translation/publications' / source / 'summary.json'
            self.assertEqual(original.read_bytes(), (self.output / 'benchmark' / destination / 'summary.json').read_bytes())
        vendor = ROOT / 'website/static/vendor/plotly'
        manifest = json.loads((vendor / 'source.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['license'], 'MIT')
        for name, record in manifest['files'].items():
            self.assertEqual(hashlib.sha256((vendor / name).read_bytes()).hexdigest(), record['sha256'])

    def test_source_links_are_mapped_or_sent_to_github(self):
        self.assertEqual(self.site.destination('../README.md#community', GUIDE), DEFAULT_BASE + '#community')
        self.assertEqual(self.site.destination('#selection-translation', GUIDE), DEFAULT_BASE + 'guide/selection-translation/#selection-translation')
        self.assertEqual(self.site.destination('https://example.com/v1', GUIDE), 'https://example.com/v1')
        self.assertIn('/blob/main/server/README.md', self.site.destination('../server/README.md', GUIDE))
        with self.assertRaises(ValueError):
            self.site.destination('#missing-anchor', GUIDE)

    def test_changelog_has_release_links_and_source_version(self):
        html = (self.output / 'changelog/index.html').read_text(encoding='utf-8')
        self.assertIn('/releases/tag/v' + self.site.version, html)
        self.assertIn('当前版本 v' + self.site.version, html)

    def test_github_alerts_and_fenced_commands_render(self):
        html = (self.output / 'guide/index.html').read_text(encoding='utf-8')
        self.assertNotIn('[!TIP]', html)
        self.assertIn('<strong>提示</strong>', html)
        macos = (self.output / 'guide/macos/index.html').read_text(encoding='utf-8')
        self.assertIn('brew install --build-from-source', ''.join(CodeText(macos).parts))
        self.assertIn('language-bash', macos)

    def test_code_blocks_have_language_and_syntax_without_browser_scripts(self):
        macos = (self.output / 'guide/macos/index.html').read_text(encoding='utf-8')
        self.assertIn('<span class="code-language">Bash</span>', macos)
        self.assertIn('<span class="tok-nf">brew</span>', macos)
        self.assertIn('<span class="tok-na">--version</span>', macos)
        api = (self.output / 'guide/api-configuration/index.html').read_text(encoding='utf-8')
        self.assertIn('<span class="code-language">JSON</span>', api)
        self.assertIn('<span class="tok-mi">4096</span>', api)

    def test_highlighting_preserves_copyable_source_and_escapes_unknown_languages(self):
        for language, source in [('bash', 'brew tap study-233/formula\n'),
                                 ('json', '{"example": "<script>alert(1)</script>", "n": 42}\n'),
                                 ('unknown-language', '<script>alert(1)</script>\n  keep spaces  \n'),
                                 ('', 'plain text & <tags>\n')]:
            with self.subTest(language=language):
                html = self.site.md.render(f'```{language}\n{source}```\n')
                self.assertEqual(''.join(CodeText(html).parts), source)
                self.assertNotIn('<script>', html)
        html = self.site.md.render('```<img/src=x>\ntext\n```\n')
        self.assertNotIn('<img', html)

    def test_duplicate_or_unsafe_configuration_is_rejected(self):
        with self.assertRaises(ValueError):
            guide_sections('<a id="same"></a>\n\n<a id="same"></a>')
        with self.assertRaises(ValueError):
            Website('//another-host/')

    def test_only_guide_content_is_indexed(self):
        index = json.loads((self.output / 'search-index.json').read_text(encoding='utf-8'))
        self.assertTrue(any('划词' in record['title'] for record in index))
        self.assertTrue(all(record['url'].startswith(DEFAULT_BASE + 'guide/') for record in index))


if __name__ == '__main__':
    unittest.main()
