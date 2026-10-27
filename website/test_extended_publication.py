"""Recompute all added-model public metrics from redacted, hash-bound evidence."""
import csv
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import unittest

ROOT=next(p for p in Path(__file__).resolve().parents if (p/'benchmarks/translation/publications').is_dir())
PUBLIC=ROOT/'benchmarks/translation/publications/2026-10-26-v2'
OLD=ROOT/'benchmarks/translation/publications/2026-10-25-v2'
IDS={'Qwen/Qwen3.8-Flash','xiaomi/mimo-v2.6-pro'}
WEIGHTS={'semantic_accuracy':40,'completeness':20,'terminology':20,'fluency':10,'structure_preservation':10}


def csv_rows(path):
    with path.open(encoding='utf-8-sig',newline='') as f:
        return list(csv.DictReader(f))


def percentile(values,fraction):
    values=sorted(values)
    return values[max(0,math.ceil(len(values)*fraction)-1)]


class ExtendedPublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.summary=json.loads((PUBLIC/'summary.json').read_text())
        cls.calls=csv_rows(PUBLIC/'calls.csv')
        cls.scores=csv_rows(PUBLIC/'new-model-quality-scores.csv')
        cls.models={m['id']:m for m in cls.summary['models']}

    def test_preserves_every_original_configuration_and_call(self):
        old=json.loads((OLD/'summary.json').read_text())
        for m in old['models']:
            self.assertEqual(self.models[m['id']],m)
        old_calls=csv_rows(OLD/'calls.csv')
        self.assertEqual(self.calls[:len(old_calls)],old_calls)
        self.assertEqual(self.summary['accounting'],old['accounting'])
        self.assertEqual(self.summary['chatgpt_accounting'],old['chatgpt_accounting'])
        self.assertEqual(len(self.models),10)
        self.assertEqual(self.summary['published_scope']['models'],9)
        self.assertEqual(self.summary['published_scope']['configurations'],10)
        self.assertEqual(self.summary['dataset_sha256'],old['dataset_sha256'])
        self.assertEqual(self.summary['published_scope']['all_attempts'],len(self.calls))
        self.assertEqual(self.summary['published_scope']['translation_calls'],old['published_scope']['translation_calls']+566)

    def test_exact_budget_and_no_repeated_translation_job(self):
        selected=[c for c in self.calls if c['model'] in IDS and c['kind'].startswith('translation-')]
        self.assertEqual(len(selected),566)
        for model in IDS:
            calls=[c for c in selected if c['model']==model]
            self.assertEqual(Counter(c['kind'] for c in calls),{'translation-precheck':3,'translation-main':240,'translation-repeat':40})
            self.assertEqual(len({c['job'] for c in calls}),283)
            self.assertTrue(all(c['status'] in ('ok','error') for c in calls))
            entry=self.models[model]
            self.assertEqual(entry['parameters'],{'temperature':0,'max_tokens':8192})
            self.assertEqual(entry['timeout_seconds'],120)
            self.assertEqual(entry['protocol'],'chat_completions')
            # The provider used both the requested ID and its lowercase Qwen alias.
            expected=[model, 'qwen3.8-flash'] if model=='Qwen/Qwen3.8-Flash' else [model]
            self.assertEqual(entry['response_models'],expected)

    def test_full_successful_quality_and_serious_error_rates(self):
        self.assertEqual(len(self.scores),480)
        for model in IDS:
            scores=[s for s in self.scores if s['model']==model]
            self.assertEqual(len(scores),240)
            self.assertEqual(len({s['job_id'] for s in scores}),240)
            entry=self.models[model]
            calls={c['job']:c for c in self.calls if c['model']==model and c['kind']=='translation-main'}
            for row in scores:
                self.assertEqual(row['result_sha256'],calls['model:'+row['job_id']]['receipt_sha256'])
                self.assertEqual(row['status'],calls['model:'+row['job_id']]['status'])
                if row['review_status']=='no_output_failure':
                    self.assertEqual(row['status'],'error')
                    self.assertEqual(float(row['score']),0)
                    self.assertTrue(all(row[d]=='' for d in WEIGHTS))
                else:
                    self.assertEqual(row['review_status'],'reviewed')
                    expected=sum(int(row[d])*w/4 for d,w in WEIGHTS.items())
                    self.assertEqual(float(row['score']),expected)
            self.assertAlmostEqual(sum(float(s['score']) for s in scores)/240,entry['quality'])
            success=[s for s in scores if s['status']=='ok']
            reviewed=[s for s in scores if s['review_status']=='reviewed']
            self.assertEqual(len(success),entry['successes'])
            self.assertEqual(len(reviewed),entry['reviewed_outputs'])
            self.assertAlmostEqual(sum(float(s['score']) for s in success)/len(success),entry['successful_quality'])
            self.assertAlmostEqual(sum(int(s['major_errors'])>0 for s in reviewed)/len(reviewed),entry['serious_error_rate'])

    def test_successful_main_latency_nearest_rank_and_measured_usage(self):
        for model in IDS:
            entry=self.models[model]
            calls=[c for c in self.calls if c['model']==model and c['kind'].startswith('translation-')]
            main=[c for c in calls if c['kind']=='translation-main' and c['status']=='ok']
            for key,column,fraction in [('p50','elapsed_seconds',.5),('p95','elapsed_seconds',.95),('ttft_p50','ttft_seconds',.5),('ttft_p95','ttft_seconds',.95)]:
                values=[float(c[column]) for c in main if c[column]]
                self.assertAlmostEqual(percentile(values,fraction),entry[key])
            rates=entry['prices_per_million']
            known=[c for c in calls if c['usage_priced_usd']!='']
            self.assertEqual(len(known),entry['priced_usage_calls'])
            self.assertEqual(283-len(known),entry['unknown_usage_calls'])
            self.assertAlmostEqual(sum(float(c['usage_priced_usd']) for c in known),entry['known_token_priced_usd'])
            for c in known:
                incoming,outgoing=int(c['input_tokens']),int(c['output_tokens'])
                cached=int(c['cached_input_tokens'] or 0)
                expected=((incoming-cached)*rates['input']+cached*rates['cached_input']+outgoing*rates['output'])/1000000
                self.assertAlmostEqual(float(c['usage_priced_usd']),expected,places=12)
            for column,key in [('input_tokens','input'),('output_tokens','output'),('cached_input_tokens','cached_input'),('reasoning_tokens','reasoning')]:
                self.assertEqual(sum(int(c[column]) for c in calls if c[column]),entry['usage_tokens'][key])
        accounting=json.loads((PUBLIC/'new-model-usage.json').read_text())
        self.assertEqual(accounting,self.summary['new_model_accounting'])
        self.assertIsNone(accounting['cash_usd'])
        self.assertTrue(accounting['reasoning_included_in_output'])

    def test_cancelled_hy3_is_accounted_for_and_excluded_from_rankings(self):
        self.assertNotIn('tencent/hy3-paid',self.models)
        cancellation=json.loads((PUBLIC/'cancelled-models.json').read_text())['models'][0]
        calls=[c for c in self.calls if c['model']=='tencent/hy3-paid']
        self.assertEqual(len(calls),cancellation['dispatched_calls'])
        self.assertTrue(all(c['kind'].startswith('cancelled-translation-') for c in calls))
        self.assertEqual(sum(c['status']=='unknown' for c in calls),cancellation['unknown_outcome_calls'])
        self.assertAlmostEqual(sum(float(c['usage_priced_usd']) for c in calls if c['usage_priced_usd']),cancellation['known_token_priced_usd'])
        self.assertFalse(cancellation['included_in_rankings'])
        self.assertEqual(cancellation['status'],'cancelled_by_user')

    def test_review_binding_hashes_and_private_data_redaction(self):
        review=json.loads((PUBLIC/'new-model-review-summary.json').read_text())
        self.assertFalse(review['independent'])
        for model in IDS:
            r=review['models'][model]
            entry=self.models[model]
            self.assertEqual(r['reviewed_outputs'],r['initial_items'])
            self.assertEqual(r['reviewed_outputs'],r['second_pass_items'])
            self.assertEqual(r['review_sha256'],entry['bindings']['review_sha256'])
            self.assertEqual(r['audit_samples'],entry['audit_samples'])
            self.assertEqual(r['audit_samples'],r['serious_outputs']+math.ceil((r['reviewed_outputs']-r['serious_outputs'])*.2))
            for key in ('review_sha256','audit_sha256','engineering_adjudication_sha256'):
                self.assertRegex(r[key],r'^[0-9a-f]{64}$')
        proof=json.loads((PUBLIC/'evidence.json').read_text())
        for name,expected in proof['public_files'].items():
            self.assertEqual(hashlib.sha256((PUBLIC/name).read_bytes()).hexdigest(),expected)
        for p in PUBLIC.iterdir():
            text=p.read_text(encoding='utf-8-sig')
            for bad in ('/Users/','.local-dev','Bearer ','Authorization','apiKey','access_token','refresh_token','source_text'):
                self.assertNotIn(bad,text,p.name)
        self.assertFalse(self.summary['method']['independent'])
        self.assertFalse(self.summary['method']['baseline_promoted'])

if __name__=='__main__':unittest.main()
