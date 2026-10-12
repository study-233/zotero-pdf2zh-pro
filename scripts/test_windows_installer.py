"""Validate the real installer payload and reject corruption before any execution."""
import hashlib
import os
import io
from pathlib import Path
import tempfile
import unittest
import zipfile

from build_windows_installer import FOOTER, MAGIC, verify_installer

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = Path(os.environ.get("PDF2ZH_TEST_INSTALLER", ROOT / "dist/zotero-pdf2zh-pro-windows-x64-setup.exe"))


@unittest.skipUnless(INSTALLER.is_file(), "Build the Windows installer first")
class InstallerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = INSTALLER.read_bytes()
        size, _, _ = FOOTER.unpack(cls.data[-FOOTER.size:])
        cls.stub = cls.data[:-FOOTER.size - size]
        cls.payload = cls.data[-FOOTER.size - size:-FOOTER.size]
        with zipfile.ZipFile(io.BytesIO(cls.payload)) as archive:
            import json
            cls.version = json.loads(archive.read("windows-dependencies.json"))["version"]

    def checked_mutation(self, change):
        target = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(self.payload)) as source, zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as output:
            for name in source.namelist():
                new_name, contents = change(name, source.read(name))
                output.writestr(new_name, contents)
        payload = target.getvalue()
        return self.stub + payload + FOOTER.pack(len(payload), hashlib.sha256(payload).digest(), MAGIC)

    def assert_rejected(self, data):
        with tempfile.TemporaryDirectory(prefix="pdf2zh-installer-test-") as directory:
            path = Path(directory) / "setup.exe"
            path.write_bytes(data)
            with self.assertRaises((ValueError, AssertionError, KeyError, zipfile.BadZipFile)):
                verify_installer(path, self.version)

    def test_single_exe_contains_a_verified_controller_lock_and_service_wheel(self):
        report = verify_installer(INSTALLER, self.version)
        self.assertEqual(report["size"], len(self.data))
        self.assertGreater(report["payloadSize"], report["bootstrapSize"])

    def test_truncated_or_corrupted_download_is_rejected(self):
        self.assert_rejected(self.data[:-10])
        corrupted = bytearray(self.data)
        corrupted[len(self.stub) + 100] ^= 1
        self.assert_rejected(bytes(corrupted))

    def test_installer_budget_is_enforced_before_payload_processing(self):
        with tempfile.TemporaryDirectory(prefix="pdf2zh-size-test-") as directory:
            path=Path(directory)/"oversized.exe"
            with path.open("wb") as stream:
                stream.truncate(40_000_001)
            with self.assertRaisesRegex(ValueError,"40 MB"):
                verify_installer(path,self.version)

    def test_repacked_wrong_wheel_cannot_pass_the_dependency_checksum(self):
        self.assert_rejected(self.checked_mutation(lambda name, data: (name, b"wrong service") if name.endswith(".whl") else (name, data)))

    def test_path_traversal_and_mismatched_versions_are_rejected(self):
        self.assert_rejected(self.checked_mutation(lambda name, data: ("../outside.ps1", data) if name == "install.ps1" else (name, data)))
        with self.assertRaises(ValueError):
            verify_installer(INSTALLER, "0.0.0")


if __name__ == "__main__":
    unittest.main()
