import copy
from pathlib import Path
import tempfile
import unittest

from check_website_compat import BASE, check, fingerprint, source_files


class WebsiteCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / 'output'
        self.write('output/guide/index.html', '<h1 id="安装">安装</h1>')
        self.write('output/results.csv', 'quality,cost\n80,\n')
        self.write('benchmarks/translation/site/results.csv', 'quality,cost\n80,\n')
        self.write('docs/user-guide.md', '<a id="windows"></a>\n# 使用指南\n')
        self.routes = {'base': BASE, 'commit': 'frozen',
                       'targets': {BASE + 'guide/#安装': BASE + 'guide/#%E5%AE%89%E8%A3%85'},
                       'source_documents': {'docs/user-guide.md': ['windows', '使用指南']}}
        self.assets = {'base': BASE, 'commit': 'frozen',
                       'files': {'results.csv': {**fingerprint(self.output / 'results.csv'), 'immutable': True}},
                       'source_files': source_files(self.root)}

    def write(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')

    def check(self):
        return check(self.output, self.routes, self.assets, self.root)

    def test_redesign_can_change_html_while_preserving_encoded_anchor(self):
        self.write('output/guide/index.html', '<main><h1>新布局</h1><a id="安装"></a></main>')
        self.assertEqual(self.check(), [])

    def test_missing_page_and_anchor_are_distinguished(self):
        self.write('output/guide/index.html', '<h1>安装</h1>')
        self.assertIn('Missing compatibility anchor', '\n'.join(self.check()))
        (self.output / 'guide/index.html').unlink()
        self.assertIn('Missing compatibility page', '\n'.join(self.check()))

    def test_unknown_cost_cannot_silently_become_zero(self):
        self.write('output/results.csv', 'quality,cost\n80,0\n')
        self.assertIn('Changed immutable download', '\n'.join(self.check()))

    def test_removing_download_is_a_failure(self):
        (self.output / 'results.csv').unlink()
        self.assertIn('Missing immutable download', '\n'.join(self.check()))

    def test_read_only_sources_are_checked_even_if_output_is_unchanged(self):
        self.write('benchmarks/translation/site/results.csv', 'quality,cost\n81,\n')
        self.assertIn('Changed benchmark source', '\n'.join(self.check()))

    def test_source_document_public_anchor_cannot_disappear(self):
        self.write('docs/user-guide.md', '# 使用指南\n')
        self.assertIn('Missing source compatibility anchor: docs/user-guide.md#windows', self.check())

    def test_project_base_and_output_traversal_are_rejected(self):
        for target in ('/guide/', BASE + '../docs/user-guide.md', 'https://example.org/guide/'):
            with self.subTest(target=target):
                self.routes['targets'] = {'old': target}
                self.assertTrue(self.check())

    def test_mismatched_baselines_are_rejected(self):
        assets = copy.deepcopy(self.assets)
        assets['commit'] = 'another-build'
        self.assertIn('same build', check(self.output, self.routes, assets, self.root)[0])


if __name__ == '__main__':
    unittest.main()
