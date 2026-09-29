from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from observability import TaskMetricsCollector as MetricsCollector


class BenchmarkAccountingTests(unittest.TestCase):
    def test_opt_in_ledger_contains_usage_without_model_or_credentials(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'PDF2ZH_USAGE_LEDGER_DIR': directory}):
            collector = MetricsCollector(task_id='example', provider='PRIVATE_ENDPOINT', model='PRIVATE_MODEL')
            collector.record_usage(prompt_tokens=100, completion_tokens=20, cache_hit_tokens=40,
                                   cache_miss_tokens=60, reasoning_tokens=10)
            content = (Path(directory)/'example.jsonl').read_text()
            self.assertNotIn('PRIVATE', content)
            event = json.loads(content)
            self.assertEqual(event['input'], 100)
            self.assertEqual(event['reasoning'], 10)
            self.assertNotIn('cost', collector.snapshot())

    def test_ledger_failure_does_not_break_translation(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory)/'file'
            file.write_text('not a directory')
            with patch.dict(os.environ, {'PDF2ZH_USAGE_LEDGER_DIR': str(file)}):
                collector = MetricsCollector(task_id='a', provider='p', model='m')
                collector.record_usage(prompt_tokens=1, completion_tokens=2,
                                       cache_hit_tokens=None, cache_miss_tokens=None)
            self.assertEqual(collector.snapshot()['tokens']['total'], 3)

    def test_cache_directory_override_is_used_in_new_process(self):
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, PDF2ZH_TRANSLATION_CACHE_DIR=directory)
            result = subprocess.run([sys.executable, '-c',
                'from pdf2zh_next.translator.cache import db; import os; from pathlib import Path; '
                'assert Path(db.database).parent == Path(os.environ["PDF2ZH_TRANSLATION_CACHE_DIR"])'],
                cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))


if __name__ == '__main__':
    unittest.main()
