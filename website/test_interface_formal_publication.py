"""Verify the new public cohort without model calls or private runtime files."""
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
PUBLIC = PUBLICATIONS / '2026-10-27-interface-formal-v1'
COMBINED = PUBLICATIONS / '2026-10-27-unified-v1'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def csv_rows(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


class InterfaceFormalPublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.summary = read(PUBLIC / 'summary.json')
        cls.scores = read(PUBLIC / 'sample-scores.json')
        cls.calls = csv_rows(PUBLIC / 'calls.csv')

    def test_every_formal_main_output_and_failure_remains_in_the_denominator(self):
        self.assertEqual(len(self.summary['models']), 8)
        self.assertEqual(len(self.scores), 1920)
        failed = [row for row in self.scores if row['status'] != 'ok']
        self.assertEqual(len(failed), 7)
        self.assertEqual(sum(row['scores'] is None for row in failed), 3)
        self.assertTrue(all(row['quality'] == 0 for row in failed if row['scores'] is None))
        repeats = read(PUBLIC / 'repeat-scores.json')
        self.assertEqual(len(repeats), 320)
        self.assertTrue(all(row['status'] == 'ok' for row in repeats))
        for model in self.summary['models']:
            rows = [row for row in self.scores if row['record_id'] == model['id']]
            successful = [row for row in rows if row['status'] == 'ok']
            reviewed = [row for row in rows if row['scores'] is not None]
            self.assertEqual(len(rows), 240)
            self.assertEqual(len({row['sample_id'] for row in rows}), 240)
            self.assertEqual(statistics.mean(row['quality'] for row in rows), model['quality'])
            self.assertEqual(len(successful), model['successes'])
            self.assertEqual(percentile([row['elapsed_seconds'] for row in successful], .5), model['p50'])
            serious = sum(any(error['severity'] in ('major', 'critical') for error in row['errors']) for row in reviewed)
            self.assertEqual(serious / len(reviewed), model['serious_error_rate'])

    def test_paired_uncertainty_and_native_identity_are_explicit(self):
        pairs = self.summary['pairs']
        self.assertEqual(len(pairs), 5)
        for pair in pairs:
            self.assertEqual(pair['samples'], 240)
            self.assertEqual(len(pair['items']), 240)
            self.assertEqual(pair['quality_ci']['clusters'], 26)
            self.assertEqual(pair['quality_ci']['rounds'], 4000)
            self.assertEqual(pair['quality_noninferiority_supported'], pair['quality_ci']['low'] >= -.5)
            self.assertEqual(pair['matched_successful_samples'], sum(row['timing_eligible'] for row in pair['items']))
        account = next(model for model in self.summary['models'] if model['id'] == 'account-responses-low')
        self.assertEqual(account['response_model_missing_main_calls'], 240)
        self.assertEqual(account['response_models'], [])
        self.assertIsNone(account['actual_cash_usd'])
        self.assertIsNone(account['known_token_priced_usd'])
        self.assertFalse(self.summary['review']['independent'])
        self.assertEqual(self.summary['engineering']['audit_unique_pairs'], 470)

    def test_ledger_preserves_rejections_unknowns_and_low_only_requests(self):
        self.assertEqual(len(self.calls), self.summary['ledger_rows'])
        reviews = [row for row in self.calls if row['kind'].startswith('quality-review-')]
        self.assertEqual(len(reviews), 770)
        self.assertEqual(self.summary['compatibility']['actual_calls'], 15)
        self.assertEqual(sum(row['kind'] == 'translation-administrative' for row in self.calls), 1)
        for row in self.calls:
            self.assertNotIn('hy3', row['model'].lower())
            parameters = json.loads(row['parameters']) if row['parameters'] else {}
            effort = parameters.get('reasoning_effort', (parameters.get('reasoning') or {}).get('effort'))
            if effort is not None:
                self.assertIn(effort, ('none', 'off', 'low'))
        missing = [row for row in self.calls if row['usage_availability'] == 'unavailable']
        self.assertTrue(missing)
        self.assertTrue(all(row['usage_priced_usd'] == '' for row in missing))

    def test_old_publications_and_scores_are_preserved_in_the_combined_view(self):
        old = read(PUBLICATIONS / '2026-10-26-unified-v1/summary.json')
        combined = read(COMBINED / 'summary.json')
        self.assertEqual(combined['models'][:16], old['models'])
        self.assertEqual(len(combined['models']), 24)
        self.assertEqual(sum(model['kind'] == 'formal' for model in combined['models']), 18)
        old_scores = read(PUBLICATIONS / '2026-10-26-unified-v1/sample-scores.json')
        self.assertEqual(read(COMBINED / 'sample-scores.json')[:len(old_scores)], old_scores)
        self.assertEqual(combined['sample_score_rows'], 2784)
        for directory in (PUBLIC, COMBINED):
            for name, expected in read(directory / 'evidence.json')['files'].items():
                self.assertEqual(hashlib.sha256((directory / name).read_bytes()).hexdigest(), expected)
            for path in directory.iterdir():
                if path.is_file():
                    text = path.read_text(encoding='utf-8-sig')
                    for private in ('Bearer ', 'refresh_token', '/Users/andy/'):
                        self.assertNotIn(private, text, path.name)


if __name__ == '__main__':
    unittest.main()
