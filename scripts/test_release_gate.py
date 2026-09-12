"""Verify downloaded release artifacts before replacing any local build output."""
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

SOURCE = Path(__file__).with_name("release.sh").read_text(encoding="utf-8")
SCRIPT = SOURCE.split("<<'VERIFY_BUILD'\n", 1)[1].split("\nVERIFY_BUILD", 1)[0]


class ReleaseGateTests(unittest.TestCase):
    def run_gate(self, change=None):
        with tempfile.TemporaryDirectory(prefix="release-gate-") as temporary:
            root = Path(temporary)
            downloaded = root / "download"
            downloaded.mkdir()
            names = ["zotero-pdf2zh-pro.xpi", "update.json", "zotero-pdf2zh-pro-windows-x64.zip",
                     "windows-update.json", "zotero-pdf2zh-pro-1.6.9-source.zip",
                     "zotero_pdf2zh_pro-1.6.9-py3-none-any.whl", "zotero_pdf2zh_pro-1.6.9.tar.gz"]
            manifest = {"version": "1.6.9", "commit": "a" * 40, "artifacts": {}}
            for name in names:
                (downloaded / name).write_bytes(name.encode())
                manifest["artifacts"][name] = {"size": len(name), "sha256": hashlib.sha256(name.encode()).hexdigest()}
            for directory in ("plugin/build", "server/dist", "dist"):
                (root / directory).mkdir(parents=True)
            target = root / "plugin/build/zotero-pdf2zh-pro.xpi"
            target.write_bytes(b"previous")
            if change:
                change(manifest, downloaded)
            (downloaded / "checksums.json").write_text(json.dumps(manifest), encoding="utf-8")
            result = subprocess.run(["node", "-", str(downloaded), "1.6.9", "a" * 40],
                                    input=SCRIPT, text=True, capture_output=True, cwd=root)
            return result.returncode, target.read_bytes()

    def test_valid_packages_replace_local_build(self):
        self.assertEqual(self.run_gate(), (0, b"zotero-pdf2zh-pro.xpi"))

    def test_corrupt_last_package_does_not_replace_first_package(self):
        code, content = self.run_gate(lambda _, d: (d / "zotero_pdf2zh_pro-1.6.9.tar.gz").write_bytes(b"bad"))
        self.assertNotEqual(code, 0)
        self.assertEqual(content, b"previous")

    def test_wrong_commit_or_unexpected_path_does_not_replace_files(self):
        for change in [lambda m, _: m.update(commit="b" * 40),
                       lambda m, _: m["artifacts"].update({"../unexpected": {}})]:
            code, content = self.run_gate(change)
            self.assertNotEqual(code, 0)
            self.assertEqual(content, b"previous")


if __name__ == "__main__":
    unittest.main()
