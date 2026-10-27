"""Recompute the unified view from its archived receipts, without API calls."""
import csv
import hashlib
import json
from pathlib import Path
import statistics
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from benchmarks.translation.report import percentile

PUBLICATIONS = ROOT / 'benchmarks/translation/publications'
PUBLIC = PUBLICATIONS / '2026-10-26-unified-v1'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def csv_rows(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


class UnifiedPublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.summary = read(PUBLIC / 'summary.json')
        cls.samples = read(PUBLIC / 'sample-scores.json')
        cls.calls = csv_rows(PUBLIC / 'calls.csv')

    def test_original_results_are_preserved_and_cancelled_model_is_unranked(self):
        models = self.summary['models']
        self.assertEqual(len({m['id'] for m in models}), 16)
        self.assertEqual(sum(m['kind'] == 'formal' for m in models), 10)
        self.assertEqual(sum(m['kind'] == 'pilot' for m in models), 6)
        self.assertEqual(len({m['model_id'] for m in models}), 9)
        current = {m['id']: m for m in models}
        for archive in ('2026-10-23-v2', '2026-10-25-v2', '2026-10-26-v2'):
            for original in read(PUBLICATIONS / archive / 'summary.json')['models']:
                for key in ('quality', 'successful_quality', 'serious_error_rate', 'successes', 'samples', 'p50', 'p95', 'known_token_priced_usd'):
                    self.assertEqual(original[key], current[original['id']][key], (archive, original['id'], key))
        self.assertFalse(any(m['model_id'] == 'tencent/hy3-paid' for m in models))
        self.assertEqual(self.summary['cancelled_models'][0]['status'], 'cancelled_by_user')

    def test_all_public_scores_recompute_quality_failures_and_audits(self):
        self.assertEqual(len(self.samples), 864)
        self.assertEqual(len(csv_rows(PUBLIC / 'sample-scores.csv')), 864)
        for model in self.summary['models']:
            samples = [s for s in self.samples if s['record_id'] == model['id']]
            self.assertEqual(len(samples), model['sample_score_coverage'])
            if not samples:
                continue
            self.assertEqual(len(samples), model['samples'])
            self.assertEqual(len({s['sample_id'] for s in samples}), len(samples))
            self.assertEqual(statistics.mean(s['quality'] for s in samples), model['quality'])
            successes = [s for s in samples if s['status'] == 'ok']
            self.assertEqual(statistics.mean(s['quality'] for s in successes), model['successful_quality'])
            self.assertEqual(len(successes), model['successes'])
            self.assertEqual(percentile([s['elapsed_seconds'] for s in successes], .5), model['p50'])
            self.assertEqual(percentile([s['elapsed_seconds'] for s in successes], .95), model['p95'])
            serious = sum(any(e['severity'] in ('major', 'critical') for e in s['errors']) for s in samples)
            self.assertEqual(serious / len(successes), model['serious_error_rate'])
        qwen = [s for s in self.samples if s['record_id'] == 'Qwen/Qwen3.8-Flash']
        failed = [s for s in qwen if s['status'] != 'ok']
        self.assertEqual(len(failed), 11)
        self.assertTrue(all(s['quality'] == 0 and s['scores'] is None and s['errors'] == [] for s in failed))
        self.assertEqual(sum(s['audited'] for s in qwen), 49)

    def test_costs_and_scope_match_the_combined_ledger(self):
        self.assertEqual(len(self.calls), 4219)
        self.assertEqual(self.summary['ledger_rows'], len(self.calls))
        self.assertEqual(self.summary['published_scope']['all_attempts'], len(self.calls))
        self.assertEqual(self.summary['published_scope']['translation_calls'], 2992)
        self.assertEqual(self.summary['published_scope']['main_outputs'], 2544)
        for model in self.summary['models']:
            calls = [c for c in self.calls if c['record_id'] == model['id']]
            main = [c for c in calls if c['kind'] in ('translation-main', 'pilot-main')]
            self.assertEqual(len(main), model['samples'])
            self.assertEqual(len(calls), model['translation_calls'])
            if model['normalized_cost_estimated']:
                amount = sum((int(c['input_tokens']) * .1 + int(c['output_tokens']) * .5) / 1e6 for c in main)
                self.assertIsNone(model['known_token_priced_usd'])
            else:
                amount = sum(float(c['usage_priced_usd']) for c in main if c['usage_priced_usd'])
                self.assertEqual(sum(not c['usage_priced_usd'] for c in calls), model['unknown_usage_calls'])
                self.assertEqual(sum(not c['usage_priced_usd'] for c in main), model['main_unknown_usage_calls'])
                self.assertAlmostEqual(sum(float(c['usage_priced_usd']) for c in calls if c['usage_priced_usd']), model['known_token_priced_usd'])
            self.assertAlmostEqual(amount, model['main_priced_usd'])
            self.assertAlmostEqual(amount / model['main_source_chars'] * 1000, model['usd_per_1000_source_chars'])
            self.assertIsNone(model['actual_cash_usd'])

    def test_export_hashes_and_privacy(self):
        evidence = read(PUBLIC / 'evidence.json')
        for name, expected in evidence['files'].items():
            self.assertEqual(hashlib.sha256((PUBLIC / name).read_bytes()).hexdigest(), expected, name)
        for name, expected in evidence['inputs'].items():
            path = ROOT / name
            if path.is_file():
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), expected, name)
        for path in PUBLIC.iterdir():
            if path.is_file():
                text = path.read_text(encoding='utf-8-sig')
                for secret in ('Bearer ', 'apiKey', '/Users/andy/', 'TRANSLATION_BENCH_COMMANDCODE_KEY', 'private-key.json'):
                    self.assertNotIn(secret, text, path.name)


if __name__ == '__main__':
    unittest.main()
