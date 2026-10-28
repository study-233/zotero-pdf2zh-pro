"""Imports happen in a fresh process so no production cache is opened by these tests."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import sqlite3
from contextlib import closing


class DevelopmentPathsTests(unittest.TestCase):
    def test_historical_response_cache_is_read_in_place_and_engine_cache_is_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / 'pdf2zh_next/cache.v1.db'
            cache.parent.mkdir()
            with closing(sqlite3.connect(cache)) as connection, connection:
                connection.execute('''CREATE TABLE _translationcache (
                    id INTEGER PRIMARY KEY, translate_engine VARCHAR(20) NOT NULL,
                    translate_engine_params TEXT NOT NULL, original_text TEXT NOT NULL,
                    translation TEXT NOT NULL,
                    UNIQUE(translate_engine, translate_engine_params, original_text) ON CONFLICT REPLACE)''')
                connection.execute('INSERT INTO _translationcache VALUES (1, ?, ?, ?, ?)',
                    ('openai', '{"lang_in": "en", "lang_out": "zh"}', 'previous text', '已有译文'))
            legacy = root / 'babeldoc/cache.v1.db'
            legacy.parent.mkdir()
            legacy.write_bytes(b'preserve legacy engine cache')
            code = '''
from pdf2zh_pro.translator.cache import TranslationCache
cache = TranslationCache('openai', {'lang_out':'zh', 'lang_in':'en'})
assert cache.get('previous text') == '\u5df2\u6709\u8bd1\u6587'
cache.set('new text', 'new translation')
'''
            result = subprocess.run([sys.executable, '-c', code],
                cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
                env={**os.environ, 'PDF2ZH_TRANSLATION_CACHE_DIR': str(root)}, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            with closing(sqlite3.connect(cache)) as connection:
                self.assertEqual(connection.execute('SELECT COUNT(*) FROM _translationcache').fetchone()[0], 2)
            self.assertEqual(legacy.read_bytes(), b'preserve legacy engine cache')
            self.assertFalse((root / 'pdf2zh_pro').exists())

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
    return root.joinpath(*path.parts[1:]) if path.parts and path.parts[0] == "~" else original_expanduser(path)
patch("pathlib.Path.home", return_value=root).start()
patch("pathlib.Path.expanduser", expanduser).start()
from pdf2zh_pro.translator.cache import db as next_db
print(json.dumps([str(next_db.database)]))
'''
            result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                                    env=env, capture_output=True, text=True, check=True)
            actual = json.loads(result.stdout.strip().splitlines()[-1])
            expected = ([root / "cache/pdf2zh_next/cache.v1.db"]
                        if override else [root / ".cache/pdf2zh_next/cache.v1.db"])
            self.assertEqual(actual, list(map(str, expected)))
            for path in expected:
                self.assertTrue(path.exists(), path)
            self.assertFalse((root / ".config/pdf2zh").exists())
            self.assertFalse((root / "config").exists())
            if override:
                self.assertFalse((root / ".cache/pdf2zh_next/cache.v1.db").exists())
                self.assertFalse((root / ".cache/babeldoc/cache.v1.db").exists())

    def test_default_paths_unchanged(self):
        self.check_paths(False)

    def test_development_paths_are_separate(self):
        self.check_paths(True)


if __name__ == "__main__":
    unittest.main()
