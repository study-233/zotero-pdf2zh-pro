"""Offline checks for the audited 24-item interface/reasoning pilot, never paid calls."""
import csv
import hashlib
import json
import math
from pathlib import Path
import random
import statistics
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from benchmarks.translation.common import digest
from benchmarks.translation.report import percentile
from benchmarks.translation.runner import estimate_cost
PUB=ROOT/'benchmarks/translation/publications/2026-10-26-interface-pilot-v1'

def read(name):return json.loads((PUB/name).read_text())

class InterfacePilotTests(unittest.TestCase):
    def test_same_samples_and_recomputed_quality_speed_cost(self):
        s=read('summary.json');samples=read('samples.json');calls=read('calls.json')
        self.assertEqual((len(s['models']),len(samples),len(calls)),(6,144,192))
        ids=None
        for m in s['models']:
            items=[v for v in samples if v['configuration_id']==m['id']]
            self.assertEqual(len(items),24)
            current={v['sample_id'] for v in items}
            self.assertEqual(len(current),24)
            if ids is None:ids=current
            self.assertEqual(ids,current)
            self.assertEqual(m['quality'],statistics.mean(v['quality'] for v in items))
            self.assertEqual(m['successful_quality'],m['quality'])
            self.assertEqual(m['p50'],percentile([v['elapsed_seconds'] for v in items],.5))
            self.assertEqual(m['p95'],percentile([v['elapsed_seconds'] for v in items],.95))
            self.assertEqual(m['serious_error_rate'],sum(any(e['severity'] in ('major','critical') for e in v['errors']) for v in items)/24)
            selected=[c for c in calls if c['model']==m['model_id'] and c['parameters']==m['parameters'] and c['kind'] in ('main','precheck')]
            self.assertEqual(len(selected),27)
            self.assertAlmostEqual(m['token_priced_usd'],sum(estimate_cost(c,m['prices_per_million']) for c in selected),places=12)
            self.assertIsNone(m['actual_cash_usd'])
        with (PUB/'comparison.csv').open(encoding='utf-8-sig',newline='') as f:self.assertEqual(len(list(csv.DictReader(f))),6)
        with (PUB/'samples.csv').open(encoding='utf-8-sig',newline='') as f:self.assertEqual(len(list(csv.DictReader(f))),144)

    def test_actual_requests_missing_receipts_and_no_reasoning_inference(self):
        s=read('summary.json');calls=read('calls.json');a=s['accounting']
        self.assertEqual(len({c['call_id'] for c in calls}),192)
        self.assertEqual({k:sum(c['kind']==k for c in calls) for k in ('main','precheck','diagnostic_excluded','compatibility_rejected')},{'main':144,'precheck':18,'diagnostic_excluded':27,'compatibility_rejected':3})
        self.assertEqual(sum(c['token_priced_usd'] is None for c in calls),3)
        self.assertEqual(a['translation_unknown_usage_calls'],3)
        self.assertAlmostEqual(a['translation_known_token_priced_usd'],sum(c['token_priced_usd'] or 0 for c in calls),places=12)
        off=next(m for m in s['models'] if m['id']=='gpt-responses-none')
        self.assertEqual(off['reasoning_tokens_main'],0)
        for m in s['models']:
            if m['model_id']=='claude-haiku-5-5':self.assertIsNone(m['reasoning_tokens_main'])
        self.assertFalse(any(m['id'] in ('gpt-chat-off','glm-chat-off') for m in s['models']))
        self.assertIsNone(a['actual_cash_usd'])

    def test_current_review_hashes_seeded_audits_and_source_preservation(self):
        s=read('summary.json');reviews=read('review-scores.json');audits=read('audits.json');e=read('evidence.json')
        total=0
        self.assertFalse(s['review']['independent'])
        self.assertEqual(s['review']['outputs_per_pass'],144)
        for m in s['models']:
            rs=reviews[m['id']];a=audits[m['id']]
            self.assertEqual(len(rs),24)
            self.assertEqual(a['review_sha256'],digest(rs))
            self.assertEqual(m['review_sha256'],digest(rs))
            required={j for j,r in rs.items() if any(e['severity'] in ('major','critical') for e in r['errors'])}
            other=sorted(set(rs)-required)
            required.update(random.Random(61013).sample(other,math.ceil(len(other)*.2)))
            self.assertEqual(set(a['verdicts']),required)
            self.assertTrue(all(v['status']=='confirmed' and len(v['note'])>20 for v in a['verdicts'].values()))
            total+=len(required)
        self.assertEqual(total,35)
        for name,sha in e['exports_sha256'].items():self.assertEqual(hashlib.sha256((PUB/name).read_bytes()).hexdigest(),sha)
        for name,sha in e['original_publications_sha256'].items():self.assertEqual(hashlib.sha256((ROOT/name).read_bytes()).hexdigest(),sha)
        for name,sha in e['input_sha256'].items():
            path=ROOT/name
            if path.exists():self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),sha)

    def test_exports_do_not_contain_credentials_or_machine_paths(self):
        for p in PUB.iterdir():
            if p.is_file():
                text=p.read_text(encoding='utf-8-sig')
                for secret in ('Bearer ', 'apiKey', 'TRANSLATION_BENCH_COMMANDCODE_KEY', '/Users/andy/', 'private-key.json'):
                    self.assertNotIn(secret,text,p.name)

if __name__=='__main__':unittest.main()
