import hashlib
import json
import re
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from build_site import BENCHMARK, DEFAULT_BASE, GUIDE, ROOT, Website, guide_sections
from check_docs import parse_document
from check_site import Page, check


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
        self.assertIn('/releases/latest/download/zotero-pdf2zh-pro-windows-x64.zip', home)
        self.assertIn('本地服务与 Zotero 插件都需要安装', home)

    def test_public_benchmark_downloads_are_unchanged(self):
        for path in [BENCHMARK / name for name in ('results.json', 'results.csv', 'pdfs.json')] + list((BENCHMARK / 'pdfs').glob('*.pdf')):
            with self.subTest(path=path.name):
                output = self.output / path.relative_to(BENCHMARK)
                self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), hashlib.sha256(output.read_bytes()).digest())

    def test_theme_and_benchmark_scripts_are_shared_from_project_root(self):
        html = (self.output / 'benchmark/index.html').read_text(encoding='utf-8')
        for script in ('ui.js', 'app.js', 'pdf-viewer.js', 'site/site.js'):
            self.assertRegex(html, f'src="{re.escape(DEFAULT_BASE + script)}\\?v=[0-9a-f]{{12}}"')
        self.assertIn(f'href="{DEFAULT_BASE}results.csv"', html)
        self.assertIn('id="pdf-reader"', html)

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
        self.assertIn('brew install --build-from-source', macos)
        self.assertIn('language-bash', macos)

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
