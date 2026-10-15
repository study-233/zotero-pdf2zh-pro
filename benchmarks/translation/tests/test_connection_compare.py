"""Bounded paired scheduling is tested without calling any model."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from benchmarks.translation import connection_compare as comparison
from benchmarks.translation.common import append, read, write
from benchmarks.translation.providers import provider_identity


class ConnectionComparisonTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.before, self.after = self.root/'before.py', self.root/'after.py'
        self.before.write_text('# before'); self.after.write_text('# after')
        self.config = {'provider': 'chatgpt', 'model': 'm', 'auth_directory': str(self.root/'auth'),
                       'limits': {'max_requests': 24, 'max_seconds': 60}}
        self.calls = []
        self.enterContext(patch.object(comparison, 'verify', return_value={'content_sha256':'data'}))
        self.enterContext(patch.object(comparison, 'code_identity', return_value={'fixture': True}))

    def fake_run(self, data, config, suite, output):
        self.calls.append((config, output))
        output.mkdir(parents=True)
        for i in range(4):
            append(output/'results.jsonl', {'status':'ok','elapsed_seconds':1})
        append(output/'runtime/ledger/task.jsonl', {'event':'request_started','callId':'one'})
        write(output/'run.json', {'status':'completed'})
        return {'status':'completed'}

    def run_comparison(self, **kwargs):
        return comparison.run(self.root/'data', self.config, self.before, self.after, self.root/'output', **kwargs)

    def test_preview_has_no_live_calls_or_output(self):
        with patch.object(comparison, 'run_pdf') as run:
            self.assertFalse(self.run_comparison()['executes_model'])
        run.assert_not_called()
        self.assertFalse((self.root/'output').exists())

    def test_three_pairs_alternate_order_and_share_exact_warm_seed(self):
        with patch.object(comparison, 'run_pdf', side_effect=self.fake_run):
            result = self.run_comparison(execute=True, max_requests=24)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['used_requests'], 12)
        self.assertEqual([p.name for _, p in self.calls[:6]], [
            'round-1-before-cold','round-1-after-cold','round-1-before-warm','round-1-after-warm',
            'round-2-after-cold','round-2-before-cold'])
        for index in range(1,4):
            seeds = [c['cache_seed'] for c,p in self.calls if p.name.startswith(f'round-{index}-') and c['cache']=='warm']
            self.assertEqual(len(seeds),2); self.assertEqual(seeds[0],seeds[1])
        self.assertEqual([c['limits']['max_requests'] for c,_ in self.calls], list(range(24,12,-1)))
        self.assertFalse(result['baseline_promoted'])
        # Loaded source identity is separate from the working tree's current source.
        for config,_ in self.calls:
            with patch('benchmarks.translation.providers.cache_seed_identity', return_value={}):
                self.assertEqual(provider_identity(config)['loaded_client_sha256'], config['client_sha256'])

    def test_shared_budget_stops_without_replaying_existing_run(self):
        with patch.object(comparison, 'run_pdf', side_effect=self.fake_run):
            with self.assertRaisesRegex(ValueError, 'budget'): self.run_comparison(execute=True, max_requests=1)
        self.assertEqual(len(self.calls),1)
        result = read(self.root/'output/comparison.json')
        self.assertEqual((result['status'],result['used_requests']),('stopped',1))
        with self.assertRaises(FileExistsError): self.run_comparison(execute=True)

    def test_incomplete_run_stops_before_next_variant(self):
        def fail(*args):
            self.fake_run(*args)
            return {'status':'failed'}
        with patch.object(comparison, 'run_pdf', side_effect=fail):
            with self.assertRaisesRegex(ValueError, 'Incomplete'): self.run_comparison(execute=True)
        self.assertEqual(len(self.calls),1)
        self.assertEqual(read(self.root/'output/comparison.json')['status'],'stopped')

    def stop_before_second_run(self):
        def stop(*args):
            if self.calls:
                raise ValueError('preflight failed before submission')
            return self.fake_run(*args)
        with patch.object(comparison, 'run_pdf', side_effect=stop):
            with self.assertRaisesRegex(ValueError, 'preflight'):
                self.run_comparison(execute=True, max_requests=24)

    def test_resume_skips_completed_runs_and_preserves_shared_budget(self):
        self.stop_before_second_run()
        with patch.object(comparison, 'run_pdf', side_effect=self.fake_run):
            result = self.run_comparison(execute=True, resume=True, max_requests=24)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['used_requests'], 12)
        self.assertEqual(len(self.calls), 12)
        self.assertEqual(len({p.name for _,p in self.calls}), 12)
        self.assertEqual(self.calls[1][0]['limits']['max_requests'], 23)

    def test_resume_rejects_config_or_frozen_client_drift_before_submitting(self):
        self.stop_before_second_run()
        with patch.object(comparison, 'run_pdf') as live:
            self.config['model'] = 'changed'
            with self.assertRaisesRegex(ValueError, 'configuration drift'):
                self.run_comparison(execute=True, resume=True, max_requests=24)
            self.config['model'] = 'm'
            (self.root/'output/before_client.py').write_text('# changed frozen client')
            with self.assertRaisesRegex(ValueError, 'client drift'):
                self.run_comparison(execute=True, resume=True, max_requests=24)
        live.assert_not_called()

    def test_resume_rejects_unreconciled_task_directory(self):
        self.stop_before_second_run()
        (self.root/'output/round-1-after-cold').mkdir()
        with patch.object(comparison, 'run_pdf') as live:
            with self.assertRaisesRegex(ValueError, 'reconcile'):
                self.run_comparison(execute=True, resume=True, max_requests=24)
        live.assert_not_called()


if __name__ == '__main__': unittest.main()
