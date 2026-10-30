"""Protect single-source tutorial content and readable compatibility documents."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from check_docs import parse_document
from sync_guide_docs import ROOT, SOURCE, compatibility_body, generated_documents, sync


class GuideSourceTests(unittest.TestCase):
    def test_checked_in_guides_are_current_and_keep_frozen_anchors(self):
        self.assertEqual(sync(check=True), [])
        baseline = json.loads((ROOT / 'website/legacy-routes.json').read_text(encoding='utf-8'))
        for source, body in generated_documents().items():
            self.assertTrue(set(baseline['source_documents'].get(source, [])) <= parse_document(body).anchors)

    def test_stale_documents_fail_without_overwriting_authored_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(ROOT / SOURCE, root / SOURCE)
            shutil.copy(ROOT / 'website/routes.json', root / 'website/routes.json')
            (root / 'docs').mkdir()
            sync(root)
            chapter = root / SOURCE / 'windows.mdx'
            chapter.write_text(chapter.read_text(encoding='utf-8') + '\n测试补充说明。\n', encoding='utf-8')
            expected = chapter.read_bytes()
            with self.assertRaisesRegex(ValueError, 'stale'):
                sync(root, check=True)
            self.assertIn('docs/user-guide.md', sync(root))
            self.assertEqual(chapter.read_bytes(), expected)
            self.assertIn('测试补充说明。', (root / 'docs/user-guide.md').read_text(encoding='utf-8'))

    def test_website_links_become_source_links_without_rewriting_code(self):
        routes = [{'slug':'windows', 'anchor':'windows'}, {'slug':'glossary', 'source':'docs/glossary-downloads.md'}]
        link = '[Windows](/zotero-pdf2zh-pro/guide/windows/#安装)'
        body = f'## 操作\n\n{link}\n\n`{link}`\n\n```md\n{link}\n## 原样标题\n```\n\n[术语库](/zotero-pdf2zh-pro/guide/glossary/)\n'
        result = compatibility_body(body, 'docs/user-guide.md', routes)
        self.assertIn('### 操作', result)
        self.assertIn('[Windows](#安装)', result)
        self.assertIn(f'`{link}`', result)
        self.assertIn(f'```md\n{link}\n## 原样标题\n```', result)
        self.assertIn('[术语库](glossary-downloads.md)', result)

    def test_export_page_is_independent_and_retains_old_entry(self):
        routes = json.loads((ROOT / 'website/routes.json').read_text(encoding='utf-8'))
        self.assertEqual(len([r for r in routes if r['slug'] == 'export']), 1)
        body = (ROOT / SOURCE / 'usage.mdx').read_text(encoding='utf-8')
        self.assertIn('id="导出-markdownjson-和-txt"', body)
        self.assertIn('/guide/export/', body)
        exported = (ROOT / SOURCE / 'export.mdx').read_text(encoding='utf-8')
        for condition in ('不调用翻译模型', '512 MiB', '5 分钟', 'OCR', '更新本地服务'):
            self.assertIn(condition, exported)


if __name__ == '__main__':
    unittest.main()
