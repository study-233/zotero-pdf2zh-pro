"""Imports happen in a fresh process so no production cache is opened by these tests."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class DevelopmentPathsTests(unittest.TestCase):
    def check_paths(self, override):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            env = {**os.environ, "PDF2ZH_TEST_HOME": str(root)}
            for key in ("PDF2ZH_CONFIG_DIR", "PDF2ZH_TRANSLATION_CACHE_DIR"):
                env.pop(key, None)
            if override:
                env.update(PDF2ZH_CONFIG_DIR=str(root / "config"), PDF2ZH_TRANSLATION_CACHE_DIR=str(root / "cache"))
            code = '''
import json, os
from pathlib import Path
from unittest.mock import patch
root = Path(os.environ["PDF2ZH_TEST_HOME"])
original_expanduser = Path.expanduser
def expanduser(path):
    return root / str(path)[2:] if str(path).startswith("~/") else original_expanduser(path)
patch("pathlib.Path.home", return_value=root).start()
patch("pathlib.Path.expanduser", expanduser).start()
from pdf2zh_next.const import DEFAULT_CONFIG_DIR
from pdf2zh_next.translator.cache import db as next_db
from babeldoc.translator.cache import db as babel_db
print(json.dumps([str(DEFAULT_CONFIG_DIR),str(next_db.database),str(babel_db.database)]))
'''
            result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                                    env=env, capture_output=True, text=True, check=True)
            actual = json.loads(result.stdout.strip().splitlines()[-1])
            expected = ([root / "config", root / "cache/pdf2zh_next/cache.v1.db", root / "cache/babeldoc/cache.v1.db"]
                        if override else [root / ".config/pdf2zh", root / ".cache/pdf2zh_next/cache.v1.db", root / ".cache/babeldoc/cache.v1.db"])
            self.assertEqual(actual, list(map(str, expected)))
            for path in expected:
                self.assertTrue(path.exists(), path)
            if override:
                self.assertFalse((root / ".cache/pdf2zh_next/cache.v1.db").exists())
                self.assertFalse((root / ".cache/babeldoc/cache.v1.db").exists())

    def test_default_paths_unchanged(self):
        self.check_paths(False)

    def test_development_paths_are_separate(self):
        self.check_paths(True)


if __name__ == "__main__":
    unittest.main()
