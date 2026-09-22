"""Exercise the actual public PyPI content guard before installer publication."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


SOURCE = Path(__file__).with_name("release.sh").read_text(encoding="utf-8")
SCRIPT = SOURCE.split("// VERIFY_PYPI\n", 1)[1].split("// END_VERIFY_PYPI", 1)[0]
VERSION = "1.7.0"


class PublicPyPIGateTests(unittest.TestCase):
    def run_gate(self, change=None):
        with tempfile.TemporaryDirectory(prefix="release-pypi-gate-") as directory:
            root = Path(directory)
            artifacts = root / "server/dist"
            artifacts.mkdir(parents=True)
            files = []
            for name in [f"zotero_pdf2zh_pro-{VERSION}-py3-none-any.whl",
                         f"zotero_pdf2zh_pro-{VERSION}.tar.gz"]:
                payload = name.encode()
                (artifacts / name).write_bytes(payload)
                files.append({"filename": name, "size": len(payload),
                              "digests": {"sha256": hashlib.sha256(payload).hexdigest()}})
            if change:
                change(files)
            return subprocess.run(["node", "-e", SCRIPT, VERSION], cwd=root,
                                  input=json.dumps({"urls": files}), text=True,
                                  capture_output=True)

    def test_existing_release_requires_both_matching_verified_files(self):
        self.assertEqual(self.run_gate().returncode, 0)
        self.assertEqual(self.run_gate(lambda files: files.pop()).returncode, 1)

    def test_same_filename_with_wrong_hash_or_size_is_a_fatal_conflict(self):
        for change in [lambda files: files[0]["digests"].update(sha256="0" * 64),
                       lambda files: files[1].update(size=1)]:
            result = self.run_gate(change)
            self.assertEqual(result.returncode, 2)
            self.assertIn("PyPI content mismatch", result.stderr)

    def test_partial_release_does_not_hide_a_conflicting_existing_file(self):
        def change(files):
            files.pop(0)
            files[0]["digests"]["sha256"] = "0" * 64
        self.assertEqual(self.run_gate(change).returncode, 2)


if __name__ == "__main__":
    unittest.main()
