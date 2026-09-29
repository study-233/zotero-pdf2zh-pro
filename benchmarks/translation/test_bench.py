import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent))
from bench import cost, request_rates, ledger_cost, recover_submission, safe_task, write, account_budget_bound


class BenchmarkTests(unittest.TestCase):
    rates = {'input': 1, 'output': 2, 'cacheRead': .1}

    def test_reasoning_is_not_double_counted_and_cache_is_discounted(self):
        metrics = {'tokens': {'availability': 'complete', 'input': 1000, 'output': 500, 'reasoning': 400},
                   'providerCache': {'availability': 'complete', 'hitTokens': 800}}
        self.assertAlmostEqual(cost(metrics, self.rates), .00128)

    def test_unknown_partial_and_invalid_usage_are_not_free(self):
        for tokens in ({}, {'availability': 'partial', 'input': 100, 'output': 10},
                       {'availability': 'complete', 'input': float('nan'), 'output': 10}):
            self.assertIsNone(cost({'tokens': tokens}, self.rates))

    def test_unknown_cache_is_conservatively_uncached(self):
        self.assertAlmostEqual(cost({'tokens': {'availability': 'complete', 'input': 1000, 'output': 500}}, self.rates), .002)

    def test_context_tiers_and_peak_windows(self):
        model = {**self.rates, 'priceDetails': {'tiers': [
            {'context': '≤ 32K', 'rates': self.rates},
            {'context': '> 32K', 'rates': {'input': 3, 'output': 4, 'cacheRead': .3}}]}}
        timestamp = datetime(2026, 9, 29, 6, tzinfo=timezone.utc).timestamp()
        self.assertEqual(request_rates(model, 32000, timestamp)['input'], 1)
        self.assertEqual(request_rates(model, 32001, timestamp)['input'], 3)
        model['priceDetails']['timeOfDay'] = {'peak': {'input': 8}, 'offPeak': {'input': 4}}
        self.assertEqual(request_rates(model, 10, timestamp)['input'], 8)
        self.assertEqual(request_rates(model, 10, timestamp+4*3600)['input'], 4)

    def test_ledger_must_match_task_totals(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work/'usage').mkdir()
            (work/'usage'/'task.jsonl').write_text(json.dumps({'input':100,'output':20,'hit':0,'miss':100,'timestamp':0})+'\n')
            metrics={'tokens': {'input':100,'output':20,'availability':'complete'}}
            self.assertAlmostEqual(ledger_cost(work,'task',self.rates,metrics), .00014)
            metrics['tokens']['input']=101
            self.assertIsNone(ledger_cost(work,'task',self.rates,metrics))

    def test_unknown_generation_is_distinct_from_explicit_auth_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            work=Path(directory);(work/'usage').mkdir()
            (work/'usage'/'task.jsonl').write_text(json.dumps({'input':None,'output':None,'timestamp':0})+'\n')
            metrics={'tokens':{'availability':'unavailable'},'requests':{'statusCodes':{'403':1}}}
            self.assertEqual(ledger_cost(work,'task',self.rates,metrics),0)
            metrics['requests']['statusCodes']={'200':1}
            self.assertIsNone(ledger_cost(work,'task',self.rates,metrics))

    def test_reconcile_never_resubmits_ambiguous_task(self):
        class Response:
            is_success=True
            def json(self): return {'tasks': self.tasks}
        class Client:
            def get(self, _): return response
        response=Response()
        response.tasks=[{'fileName':'unique.pdf','taskId':'one'}]
        self.assertEqual(recover_submission(Client(),'http://local',{'fileName':'unique.pdf'}),'one')
        for tasks in ([], response.tasks*2):
            response.tasks=tasks
            with self.assertRaises(RuntimeError):
                recover_submission(Client(),'http://local',{'fileName':'unique.pdf'})

    def test_task_allowlist_does_not_export_secrets_or_provider_errors(self):
        self.assertEqual(safe_task({'taskId':'a','apiKey':'SECRET','error':'SECRET','requestPayload':{'apiKey':'SECRET'}}), {'taskId':'a'})

    def test_atomic_state_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'runs.json'
            write(path,{'status':'submitting'})
            write(path,{'status':'completed'})
            self.assertEqual(json.loads(path.read_text())['status'],'completed')

    def test_account_budget_is_opt_in_and_rejects_period_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            work=Path(directory)
            self.assertIsNone(account_budget_bound(work,{},'not-used'))
            response=type('Response',(),{'is_success':True,'json':lambda self:{'totalCost':2.5}})()
            with patch('bench.client') as factory:
                factory.return_value.__enter__.return_value.get.return_value=response
                config={'accountBudget':{'approved':True,'baselineUsd':2}}
                self.assertEqual(account_budget_bound(work,config,'test-key'),.5)
                response.json=lambda:{'totalCost':2.4}
                with self.assertRaises(RuntimeError):account_budget_bound(work,config,'test-key')
                self.assertNotIn('test-key',(work/'billing-bound.json').read_text())


if __name__ == '__main__':
    unittest.main()
