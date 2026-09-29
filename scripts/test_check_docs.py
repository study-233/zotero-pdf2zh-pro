"""Regression fixtures for docs guards; no application dependencies or network."""

from pathlib import Path
import tempfile
import unittest

from check_docs import PUBLIC_ANCHORS, check_links, check_readme, parse_document


class DocumentationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.write('README.md', '<!-- release-version --> `1.2.3`\n' +
                   '\n'.join(f'<a id="{anchor}"></a>' for anchor in sorted(PUBLIC_ANCHORS)))
        self.write('plugin/package.json', '{"version":"1.2.3"}')
        self.write('server/pyproject.toml', '[project]\nversion = "1.2.3"\n')
        # Must parse statically: never import the real service or its dependencies.
        self.write('server/server.py', 'import unavailable_package\nVERSION = "1.2.3"\n')

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')

    def links(self, text):
        self.write('docs/test.md', text)
        return check_links(self.root, ['docs/test.md'])

    def test_valid_markdown_html_and_references(self):
        self.write('assets/image (1).png', 'fixture')
        self.write('docs/target.md', '# 中文 `标题`\n<a id="stable"></a>\n')
        self.assertEqual(self.links('''[首页](../README.md#usage)
[中文](target.md#%E4%B8%AD%E6%96%87-%E6%A0%87%E9%A2%98)
![图片](<../assets/image (1).png>)
![图片](../assets/image%20(1).png)
<a href="target.md#stable">HTML</a>
<img src="../assets/image%20(1).png" alt="test" />
[参考][guide] [guide][] [guide]
[guide]: target.md#stable "guide title"
[![徽章](https://example.invalid/badge)](../README.md#features)
[外部](https://example.invalid/does-not-exist) [邮件](mailto:test@example.invalid)
'''), [])

    def test_broken_file_and_image_report_location(self):
        errors = self.links('# Test\n[链接](missing.md)\n<img src="absent.png">\n')
        self.assertEqual(len(errors), 2)
        self.assertIn('docs/test.md:2: missing local target: missing.md', errors)
        self.assertIn('docs/test.md:3: missing local target: absent.png', errors)

    def test_missing_fragment_in_target(self):
        self.write('docs/target.md', '# Present\n')
        self.assertIn('docs/test.md:1: missing anchor: target.md#missing',
                      self.links('[x](target.md#missing)'))

    def test_duplicate_explicit_anchor(self):
        errors = self.links('<a id="same"></a>\n<div id="same"></div>')
        self.assertIn('docs/test.md:2: duplicate explicit anchor: same', errors)

    def test_duplicate_anchor_in_link_target(self):
        self.write('docs/target.md', '<a id="a"></a>\n<a name="a"></a>')
        self.assertIn('docs/target.md:2: duplicate explicit anchor: a',
                      self.links('[x](target.md#a)'))

    def test_heading_suffixes(self):
        self.assertEqual(self.links('# Repeat\n# Repeat\n# Repeat-1\n'
                                    '[x](#repeat-1) [y](#repeat-1-1)'), [])

    def test_ignore_code_and_comments_preserving_lines(self):
        text = '''# Test
```md
[bad](missing.md)
<a id="usage"></a>
```
~~~md
![bad](missing.png)
~~~
`[bad](missing.md)`
<!-- [bad](missing.md) -->
[real](absent.md)
'''
        self.assertEqual(self.links(text), ['docs/test.md:11: missing local target: absent.md'])
        self.assertNotIn('usage', parse_document(text).anchors)

    def test_undefined_reference_and_tasks(self):
        self.assertEqual(self.links('- [ ] pending\n- [x] done\n'), [])
        self.assertEqual(self.links('[label][missing]'),
                         ['docs/test.md:1: undefined link reference: missing'])

    def test_missing_document(self):
        self.assertEqual(check_links(self.root, ['absent.md']),
                         ['absent.md:1: document does not exist'])

    def test_link_outside_repository(self):
        self.assertIn('link escapes repository', self.links('[x](../../outside.md)')[0])

    def test_valid_release_versions(self):
        self.assertEqual(check_readme(self.root), ([], []))

    def test_missing_duplicate_and_malformed_markers(self):
        original = (self.root / 'README.md').read_text()
        for replacement in ('', '<!-- release-version --> 1.2.3',
                            '<!-- release-version --> `1.2.3`\n<!-- release-version --> `1.2.3`'):
            with self.subTest(replacement=replacement):
                self.write('README.md', original.replace('<!-- release-version --> `1.2.3`', replacement))
                errors, _ = check_readme(self.root)
                self.assertTrue(any('expected one release version marker' in e for e in errors))

    def test_each_version_source_is_checked(self):
        for path in ('plugin/package.json', 'server/pyproject.toml', 'server/server.py'):
            with self.subTest(path=path):
                original = (self.root / path).read_text()
                self.write(path, original.replace('1.2.3', '2.0.0'))
                errors, _ = check_readme(self.root)
                self.assertTrue(any(f'differs from {path}' in e for e in errors))
                self.write(path, original)

    def test_deleted_public_anchor(self):
        path = self.root / 'README.md'
        path.write_text(path.read_text().replace('<a id="windows"></a>', ''))
        self.assertIn('README.md:1: missing public anchor: windows', check_readme(self.root)[0])

    def test_long_readme_warns_without_failing(self):
        path = self.root / 'README.md'
        path.write_text(path.read_text() + '\n' * 300)
        errors, warnings = check_readme(self.root)
        self.assertEqual(errors, [])
        self.assertEqual(len(warnings), 1)


if __name__ == '__main__':
    unittest.main()
