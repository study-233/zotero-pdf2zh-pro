"""The added ChatGPT channel must preserve old scores and unknown subscription costs."""
import csv
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT/'benchmarks/translation/publications/2026-10-25-v2'
ORIGINAL = ROOT/'benchmarks/translation/publications/2026-10-23-v2'
ENTRY = 'chatgpt/gpt-6-luna-low'


class ChatGPTPublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.summary=json.loads((PUBLIC/'summary.json').read_text())
        with (PUBLIC/'calls.csv').open(encoding='utf-8-sig',newline='') as stream:
            cls.calls=list(csv.DictReader(stream))
        cls.entry=next(m for m in cls.summary['models'] if m['id']==ENTRY)

    def test_original_seven_results_are_preserved_as_separate_configurations(self):
        original=json.loads((ORIGINAL/'summary.json').read_text())
        current={m['id']:m for m in self.summary['models']}
        self.assertEqual(len(current),8)
        self.assertEqual(self.summary['published_scope']['models'],7)
        self.assertEqual(self.summary['published_scope']['configurations'],8)
        for model in original['models']:
            for field,value in model.items():
                if field!='name':
                    self.assertEqual(current[model['id']][field],value,(model['id'],field))
        with (ORIGINAL/'calls.csv').open(encoding='utf-8-sig',newline='') as stream:
            old_calls=list(csv.DictReader(stream))
        self.assertEqual(self.calls[:len(old_calls)],old_calls)
        self.assertEqual(self.summary['accounting'],original['accounting'])

    def test_formal_calls_tokens_and_successes_agree_with_receipts(self):
        calls=[r for r in self.calls if r['model']==ENTRY and r['kind'].startswith('translation-')]
        main=[r for r in calls if r['kind']=='translation-main']
        self.assertEqual(len(calls),283)
        self.assertEqual(len(main),240)
        self.assertEqual(sum(r['status']=='ok' for r in main),self.entry['successes'])
        for column,key in [('input_tokens','input'),('output_tokens','output'),('cached_input_tokens','cached_input'),('reasoning_tokens','reasoning')]:
            self.assertEqual(sum(int(r[column]) for r in calls if r[column]),self.entry['usage_tokens'][key])
        self.assertEqual(len(self.calls),self.summary['published_scope']['all_attempts'])
        proof=self.summary['chatgpt_accounting']['ledger_confirmation']
        self.assertEqual(proof['translation_receipts_confirmed'],310)
        self.assertEqual(len(proof['redacted_entries_sha256']),64)

    def test_subscription_costs_remain_unknown(self):
        self.assertIsNone(self.entry['known_token_priced_usd'])
        accounting=self.summary['chatgpt_accounting']
        self.assertIsNone(accounting['cash_usd'])
        self.assertIsNone(accounting['quota_percent'])
        self.assertTrue(accounting['quick_gate_passed'])
        for row in self.calls:
            if row['model']==ENTRY:
                self.assertEqual(row['usage_priced_usd'],'')
                self.assertEqual(row['server_usage_usd'],'')
        self.assertFalse(self.summary['method']['independent'])
        self.assertFalse(self.summary['method']['baseline_promoted'])

    def test_quality_scores_recompute_and_bind_to_translation_receipts(self):
        with (PUBLIC/'quality-scores.csv').open(encoding='utf-8-sig',newline='') as stream:
            scores=list(csv.DictReader(stream))
        self.assertEqual(len(scores),240)
        self.assertEqual(len({row['job_id'] for row in scores}),240)
        calls={r['job']:r for r in self.calls if r['model']==ENTRY and r['kind']=='translation-main'}
        weights={'semantic_accuracy':40,'completeness':20,'terminology':20,'fluency':10,'structure_preservation':10}
        for row in scores:
            expected=sum(int(row[dimension])*weight/4 for dimension,weight in weights.items())
            self.assertEqual(float(row['score']),expected)
            self.assertEqual(row['result_sha256'],calls['model:'+row['job_id']]['receipt_sha256'])
        self.assertAlmostEqual(sum(float(row['score']) for row in scores)/240,self.entry['quality'])
        self.assertAlmostEqual(sum(int(row['major_errors'])>0 for row in scores)/240,self.entry['serious_error_rate'])

    def test_api_equivalent_estimate_uses_receipts_and_separate_scope(self):
        usage=json.loads((PUBLIC/'chatgpt-usage.json').read_text())
        self.assertEqual(usage,self.summary['chatgpt_accounting'])
        estimate=usage['api_equivalent_estimate']
        rates={key:Decimal(str(value)) for key,value in estimate['usd_per_million_tokens'].items()}
        formal=Decimal(0)
        total=Decimal(0)
        for name,stage in usage['stages'].items():
            tokens=stage['tokens']
            receipts=[r for r in self.calls if r['model']==ENTRY and r['kind']==name]
            self.assertEqual(len(receipts),stage['calls'])
            for column,value in tokens.items():
                self.assertEqual(sum(int(row[column] or 0) for row in receipts),value)
            expected=((tokens['input_tokens']-tokens['cached_input_tokens'])*rates['input']+
                      tokens['cached_input_tokens']*rates['cached_input']+tokens['output_tokens']*rates['output'])/1_000_000
            self.assertEqual(Decimal(str(stage['estimated_api_equivalent_usd'])),expected)
            total+=expected
            if name.startswith('translation-'):
                formal+=expected
        self.assertEqual(float(formal),0.0487976)
        self.assertEqual(self.entry['estimated_api_equivalent_usd'],float(formal))
        self.assertEqual(estimate['formal_translation_usd'],float(formal))
        self.assertEqual(estimate['all_recorded_usd'],float(total))
        self.assertGreater(total,formal)
        self.assertTrue(estimate['reasoning_tokens_included_in_output'])
        self.assertEqual(estimate['source'],self.summary['sources']['chatgpt_api_equivalent_prices'])

    def test_export_is_redacted_and_hash_bound(self):
        evidence=json.loads((PUBLIC/'evidence.json').read_text())
        for name,expected in evidence['public_files'].items():
            self.assertEqual(hashlib.sha256((PUBLIC/name).read_bytes()).hexdigest(),expected)
        for path in PUBLIC.iterdir():
            content=path.read_text(encoding='utf-8-sig')
            for forbidden in ('/Users/','.local-dev','Bearer ','Authorization','accountId','account_fingerprint','source_text','refresh_token','access_token'):
                self.assertNotIn(forbidden,content,path.name)


if __name__=='__main__':
    unittest.main()
