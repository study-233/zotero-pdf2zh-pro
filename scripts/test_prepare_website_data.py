"""Content migration must preserve commands, public anchors and audited data."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from build_site import Website
from prepare_website_data import release_entries, reset_generated
from website_data import EXPERIMENT_PUBLICATION, PILOT_PUBLICATION, load_benchmark


class PreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.site = Website()

    def test_release_split_preserves_warnings_links_and_legacy_ids(self):
        entries = release_entries(self.site)
        self.assertEqual(entries[0]['version'], 'v' + self.site.version)
        self.assertEqual(len(entries), len({entry['version'] for entry in entries}))
        warning = next(entry for entry in entries if entry['version'] == 'v1.9.4')
        self.assertTrue(warning['summary'].startswith('本次更新'))
        self.assertIn('HOMEBREW_NO_INSTALL_CLEANUP=1', warning['html'])
        self.assertIn('/compare/v1.9.3...v1.9.4', warning['html'])
        for entry in entries:
            self.assertTrue(entry['summary'], entry['version'])
            self.assertLessEqual(len(entry['summary']), 101)
            self.assertEqual(self.site.anchors['CHANGELOG.md', entry['anchor']], 'changelog/#' + entry['anchor'])
            for anchor in entry['anchors']:
                self.assertIn(f'id="{anchor}"', entry['html'])

    def test_generated_reset_cannot_delete_authored_content(self):
        with tempfile.TemporaryDirectory() as directory, patch('prepare_website_data.SITE', Path(directory)):
            authored = Path(directory) / 'src'
            authored.mkdir()
            sentinel = authored / 'keep.md'
            sentinel.write_text('keep', encoding='utf-8')
            for name in ('src', '..', '../src', '.generated-content/..'):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    reset_generated(name)
            self.assertEqual(sentinel.read_text(encoding='utf-8'), 'keep')
            generated = reset_generated('.generated-content')
            (generated / 'stale.md').write_text('old', encoding='utf-8')
            self.assertEqual(list(reset_generated('.generated-content').iterdir()), [])

    def test_unaudited_publications_cannot_be_prepared(self):
        read_text = Path.read_text
        for publication in (EXPERIMENT_PUBLICATION, PILOT_PUBLICATION):
            def read(path, *args, **kwargs):
                result = read_text(path, *args, **kwargs)
                if path == publication / 'summary.json':
                    result = json.dumps({**json.loads(result), 'status': 'draft'})
                return result
            with self.subTest(publication=publication.name), patch.object(Path, 'read_text', read):
                with self.assertRaisesRegex(ValueError, 'audited'):
                    load_benchmark()


if __name__ == '__main__':
    unittest.main()
