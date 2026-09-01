from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from build_windows_update_manifest import ASSET_NAME, build_manifest


class WindowsUpdateManifestTests(unittest.TestCase):
    def test_builds_manifest_from_final_package_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / ASSET_NAME
            package.write_bytes(b"windows-package")

            manifest = build_manifest("1.4.0", package)

        self.assertEqual(manifest["schemaVersion"], 1)
        self.assertEqual(manifest["version"], "1.4.0")
        self.assertEqual(manifest["size"], len(b"windows-package"))
        self.assertEqual(
            manifest["sha256"], hashlib.sha256(b"windows-package").hexdigest()
        )
        self.assertEqual(
            manifest["url"],
            "https://github.com/study-233/zotero-pdf2zh-pro/releases/download/"
            "v1.4.0/zotero-pdf2zh-pro-windows-x64.zip",
        )
        json.dumps(manifest)

    def test_rejects_prerelease_and_missing_package(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / ASSET_NAME
            package.write_bytes(b"payload")
            with self.assertRaises(ValueError):
                build_manifest("1.4.0-beta.1", package)
            with self.assertRaises(FileNotFoundError):
                build_manifest("1.4.0", Path(directory) / "missing.zip")


if __name__ == "__main__":
    unittest.main()
