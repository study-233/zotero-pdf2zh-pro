import io
from pathlib import Path
import tarfile
import tempfile
import unittest

from prepare_windows_skia_source import unpack


class SourceTests(unittest.TestCase):
    def archive(self, directory, entries):
        path = Path(directory) / 'source.tar.gz'
        with tarfile.open(path, 'w:gz') as archive:
            for name, contents, link in entries:
                item = tarfile.TarInfo(name)
                if link:
                    item.type = tarfile.SYMTYPE
                    item.linkname = link
                    archive.addfile(item)
                else:
                    item.size = len(contents)
                    archive.addfile(item, io.BytesIO(contents))
        return path

    def test_source_links_become_file_copies_without_symlink_privileges(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.archive(directory, [('skia/link/file', b'', '../target'), ('skia/target', b'source', None)])
            root = unpack(path, Path(directory) / 'output')
            self.assertEqual((root / 'link/file').read_bytes(), b'source')
            self.assertFalse((root / 'link/file').is_symlink())

    def test_escaping_paths_are_rejected_before_any_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.archive(directory, [('skia/../outside', b'bad', None)])
            output = Path(directory) / 'output'
            with self.assertRaises(ValueError):
                unpack(path, output)
            self.assertFalse(output.exists())

    def test_escaping_link_targets_are_rejected_before_any_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.archive(directory, [('skia/link', b'', '../outside')])
            output = Path(directory) / 'output'
            with self.assertRaises(ValueError):
                unpack(path, output)
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
