"""Public benchmark numbers must agree with redacted evidence, not private prose."""
import csv
import hashlib
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / 'benchmarks/translation/publications/2026-10-23-v2'


class PublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.summary = json.loads((PUBLIC / 'summary.json').read_text(encoding='utf-8'))
        with (PUBLIC / 'calls.csv').open(encoding='utf-8-sig', newline='') as stream:
            cls.calls = list(csv.DictReader(stream))

    def test_model_totals_and_partial_costs_agree_with_receipts(self):
        self.assertEqual(len(self.summary['models']), 7)
        self.assertEqual(len(self.calls), 2361)
        missing = 0
        for model in self.summary['models']:
            calls = [r for r in self.calls if r['model'] == model['id'] and r['kind'].startswith('translation-')]
            self.assertEqual(len(calls), 283)
            self.assertEqual(sum(r['kind'] == 'translation-main' for r in calls), 240)
            self.assertEqual(sum(r['kind'] == 'translation-main' and r['status'] == 'ok' for r in calls), model['successes'])
            known = [r for r in calls if r['usage_priced_usd']]
            self.assertEqual(len(known), model['receipt_complete_calls'])
            self.assertEqual(283 - len(known), model['missing_receipt_calls'])
            missing += 283 - len(known)
            self.assertAlmostEqual(sum(float(r['usage_priced_usd']) for r in known), model['known_token_priced_usd'], places=10)
            self.assertIsNone(model['exact_billed_usd'])
        self.assertEqual(missing, 59)

    def test_account_charge_is_observed_credits_and_not_claimed_cash(self):
        with (PUBLIC / 'account-ledger.csv').open(encoding='utf-8-sig', newline='') as stream:
            ledger = list(csv.DictReader(stream))
        accounting = self.summary['accounting']
        self.assertAlmostEqual(sum(float(r['usage_credits']) for r in ledger), accounting['observed_usage_credits'], places=9)
        self.assertEqual(sum(int(r['requests']) for r in ledger), accounting['recorded_requests'])
        self.assertEqual(sum(int(r['input_tokens']) for r in ledger), accounting['recorded_input_tokens'])
        self.assertEqual(sum(int(r['output_tokens']) for r in ledger), accounting['recorded_output_tokens'])
        self.assertIsNone(accounting['cash_usd'])
        self.assertFalse(accounting['per_model_allocation_verified'])
        self.assertFalse(self.summary['method']['independent'])
        self.assertFalse(self.summary['method']['baseline_promoted'])

    def test_no_paper_text_credentials_or_private_paths_are_exported(self):
        allowed = {'batch', 'kind', 'model', 'job', 'status', 'input_tokens', 'output_tokens', 'cached_input_tokens',
                   'cache_write_input_tokens', 'reasoning_tokens', 'usage_availability', 'elapsed_seconds',
                   'ttft_seconds', 'usage_priced_usd', 'receipt_sha256'}
        self.assertEqual(set(self.calls[0]), allowed)
        for p in PUBLIC.iterdir():
            text = p.read_text(encoding='utf-8-sig')
            for forbidden in ['.local-dev', 'selection_context', 'Authorization', 'Bearer ', 'C:/Users/', 'D:/StudyHub/']:
                self.assertNotIn(forbidden, text, p.name)

    def test_published_files_match_manifest(self):
        manifest = json.loads((PUBLIC / 'evidence.json').read_text(encoding='utf-8'))
        for name, expected in manifest['public_files'].items():
            self.assertEqual(hashlib.sha256((PUBLIC / name).read_bytes()).hexdigest(), expected)


if __name__ == '__main__':
    unittest.main()
