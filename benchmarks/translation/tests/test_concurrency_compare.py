"""No model calls: paired scheduling, shared limits and drift protection."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from benchmarks.translation import concurrency_compare as comparison
from benchmarks.translation.common import append, read, write


class ConcurrencyComparisonTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.config = {'provider': 'chatgpt', 'model': 'm', 'auth_directory': str(self.root/'auth'),
                       'limits': {'max_requests': 120, 'max_seconds': 1800}}
        self.calls = []
        self.enterContext(patch.object(comparison, 'verify', return_value={'content_sha256':'data'}))
        self.identity = self.enterContext(patch.object(comparison, 'code_identity', return_value={'fixture':True}))

    def fake_run(self, data, config, suite, output):
        self.calls.append(config)
        output.mkdir(parents=True)
        for i in range(4):
            append(output/'results.jsonl', {'status':'ok', 'elapsed_seconds':1})
        append(output/'runtime/ledger/task.jsonl', {'event':'request_started','callId':'one'})
        return {'status':'completed'}

    def run_comparison(self, **kwargs):
        return comparison.run(self.root/'data', self.config, self.root/'output', **kwargs)

    def test_preview_does_not_start_or_write(self):
        with patch.object(comparison, 'run_pdf') as live:
            self.assertFalse(self.run_comparison()['executes_model'])
        live.assert_not_called()
        self.assertFalse((self.root/'output').exists())

    def test_three_pairs_only_change_workers_and_share_limits(self):
        with patch.object(comparison, 'run_pdf', side_effect=self.fake_run):
            result = self.run_comparison(execute=True)
        self.assertEqual([c['pdf_options'] for c in self.calls], [{'qps':2,'poolSize':n} for n in (2,4,4,2,2,4)])
        self.assertEqual([c['limits']['max_requests'] for c in self.calls], list(range(120,114,-1)))
        self.assertEqual(len({c['client_sha256'] for c in self.calls}),1)
        self.assertTrue(all(c['cache']=='cold' and c['experimental_worker_cap']==4 for c in self.calls))
        self.assertEqual((result['status'],result['used_requests']),('completed',6))
        self.assertFalse(result['baseline_promoted'])

    def test_budget_stop_does_not_replay(self):
        with patch.object(comparison, 'run_pdf', side_effect=self.fake_run):
            with self.assertRaisesRegex(ValueError,'budget'):
                self.run_comparison(execute=True,max_requests=1)
        self.assertEqual(len(self.calls),1)
        self.assertEqual(read(self.root/'output/comparison.json')['used_requests'],1)
        with self.assertRaises(FileExistsError): self.run_comparison(execute=True)

    def test_failure_and_rate_limit_stop_next_run(self):
        def limited(*args):
            result = self.fake_run(*args)
            append(args[-1]/'runtime/ledger/task.jsonl', {'event':'request_finished','callId':'one','statusCode':429})
            return result
        with patch.object(comparison, 'run_pdf', side_effect=limited):
            with self.assertRaisesRegex(ValueError,'Rate or quota'):
                self.run_comparison(execute=True)
        self.assertEqual(len(self.calls),1)

    def test_drift_stops_before_next_call(self):
        def changed(*args):
            result = self.fake_run(*args)
            self.identity.return_value = {'fixture':False}
            return result
        with patch.object(comparison, 'run_pdf', side_effect=changed):
            with self.assertRaisesRegex(ValueError,'Source changed'):
                self.run_comparison(execute=True)
        self.assertEqual(len(self.calls),1)

    def test_incomplete_page_retains_calls_and_stops_without_replacement(self):
        def incomplete(*args):
            self.fake_run(*args)
            write(args[-1]/'run.json', {'status':'stopped_after_pdf_error'})
            return {'status':'stopped_after_pdf_error'}
        with patch.object(comparison, 'run_pdf', side_effect=incomplete):
            with self.assertRaisesRegex(ValueError,'Incomplete PDF'):
                self.run_comparison(execute=True)
        manifest = read(self.root/'output/comparison.json')
        self.assertEqual((manifest['status'],manifest['used_requests']),('stopped',1))
        self.assertEqual(len(self.calls),1)


if __name__ == '__main__': unittest.main()
