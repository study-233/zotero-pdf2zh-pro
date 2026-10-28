import tempfile
import unittest
import zipfile
from pathlib import Path

from vendor_pdf2zh_runtime import SERVER_DIR, WheelSnapshot, extract_snapshot


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.wheel = self.root / 'fixture.whl'
        self.snapshot = WheelSnapshot('fixture', '1', 'fixture', '', '', None, '', ('gui.py',))

    def archive(self, **members):
        with zipfile.ZipFile(self.wheel, 'w') as archive:
            for name, data in members.items():
                archive.writestr(name, data)

    def test_extract_is_reviewable_and_never_replaces_existing_sources(self):
        self.archive(**{'fixture/__init__.py': '# snapshot', 'fixture/gui.py': '# excluded'})
        staging = self.root / 'staged'
        extract_snapshot(self.snapshot, self.wheel, staging)
        self.assertEqual((staging / 'fixture/__init__.py').read_text(), '# snapshot')
        self.assertFalse((staging / 'fixture/gui.py').exists())
        with self.assertRaises(FileExistsError):
            extract_snapshot(self.snapshot, self.wheel, staging)
        for path in (SERVER_DIR, SERVER_DIR / 'pdf2zh_pro', SERVER_DIR.parent):
            with self.subTest(path=path), self.assertRaises(ValueError):
                extract_snapshot(self.snapshot, self.wheel, path)

    def test_traversal_is_rejected_before_writing_members(self):
        self.archive(**{'fixture/__init__.py': '# snapshot', 'fixture/../../escaped.py': '# unsafe'})
        staging = self.root / 'staged'
        with self.assertRaises(ValueError):
            extract_snapshot(self.snapshot, self.wheel, staging)
        self.assertFalse((self.root / 'escaped.py').exists())
        self.assertFalse((staging / 'fixture/__init__.py').exists())
