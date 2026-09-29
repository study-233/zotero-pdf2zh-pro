import json
from pathlib import Path
import sys
import tempfile
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent))
from bench import cost, request_rates, ledger_cost, recover_submission, safe_task, write, account_budget_bound, run, digest, now


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

    def test_supplement_preserves_originals_and_resumes_without_duplicate_spending(self):
        with tempfile.TemporaryDirectory() as directory:
            work=Path(directory);(work/'papers').mkdir();(work/'selection').mkdir()
            (work/'papers'/'p.pdf').write_bytes(b'frozen');write(work/'selection'/'p.json',[])
            write(work/'snapshot.json',{'pricingVerified':True,'config':{},'seed':1,'budgetUsd':10,
                'reserveUsd':1,'setupKnownUsd':0,'accountBudget':{'approved':True},
                'models':[{'id':'m','name':'model','catalogAvailable':True}],
                'papers':[{'id':'p','sha256':digest(work/'papers'/'p.pdf'),
                          'selectionSha256':digest(work/'selection'/'p.json')}]})
            write(work/'runs.json',[{'id':'p:m','status':'cancelled','taskId':'original','costUsd':None}])
            original=(work/'runs.json').read_bytes()
            response=lambda data:SimpleNamespace(is_success=True,json=lambda:data)
            states=iter(['running','completed'])
            def get(url):
                if url.endswith('/health'):return response({'workspace':{'path':str(work/'tasks')}})
                return response({'task':{'taskId':'new','status':next(states),'updatedAt':now(),
                    'metrics':{'tokens':{'availability':'partial'},'requests':{'succeeded':1,'failed':1}}}})
            with patch.dict(os.environ,{'COMMAND_CODE_API_KEY':'test-only'}), patch.dict(sys.modules,{'fitz':SimpleNamespace()}), \
                 patch('bench.client') as factory, patch('bench.account_budget_bound',return_value=3), \
                 patch('bench.task_body',return_value={}), patch('bench.ledger_cost',return_value=None), patch('bench.time.sleep'):
                service=factory.return_value.__enter__.return_value
                service.get.side_effect=get;service.post.return_value=response({'task':{'taskId':'new'}})
                run(work,'http://isolated',supplement=True)
                run(work,'http://isolated',supplement=True)
                service.post.assert_called_once_with('http://isolated/tasks',json={})
            self.assertEqual((work/'runs.json').read_bytes(),original)
            row=json.loads((work/'supplemental-runs.json').read_text())[0]
            self.assertEqual(row['supplementalTo'],'original')
            self.assertEqual(row['status'],'completed')
            self.assertIsNone(row['costUsd'])

    def test_runner_budget_resume_and_unknown_usage_guards(self):
        # Exercise the real runner using a fake isolated service; no network or PDF dependency.
        for scenario in ('budget', 'completed', 'unknown'):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                work=Path(directory)
                (work/'papers').mkdir();(work/'selection').mkdir()
                (work/'papers'/'p.pdf').write_bytes(b'frozen-pdf')
                write(work/'selection'/'p.json',[])
                snapshot={'pricingVerified':True,'config':{},'seed':1,'budgetUsd':10,'reserveUsd':1,
                    'setupKnownUsd':0,'models':[{'id':'m','name':'model','catalogAvailable':True}],
                    'papers':[{'id':'p','sha256':digest(work/'papers'/'p.pdf'),
                               'selectionSha256':digest(work/'selection'/'p.json')}]}
                write(work/'snapshot.json',snapshot)
                if scenario=='completed':
                    write(work/'runs.json',[{'id':'p:m','status':'completed','taskId':'existing'}])
                response=lambda data:SimpleNamespace(is_success=True,json=lambda:data)
                task={'taskId':'new','status':'running','stage':'Translate', 'updatedAt':now(),
                      'metrics':{'tokens':{'availability':'partial'},'requests':{'succeeded':1,'failed':1,'statusCodes':{'200':1,'unknown':1}}}}
                posts=[]
                def post(url, **kwargs):
                    posts.append(url)
                    if url.endswith('/cancel'):task['status']='cancelled'
                    return response({'task':dict(task)})
                def get(url):
                    if url.endswith('/health'):return response({'workspace':{'path':str(work/'tasks')}})
                    return response({'task':dict(task)})
                with patch.dict(os.environ,{'COMMAND_CODE_API_KEY':'test-only'}), patch.dict(sys.modules,{'fitz':SimpleNamespace()}), \
                     patch('bench.client') as factory, patch('bench.account_budget_bound',return_value=8.5 if scenario=='budget' else None), \
                     patch('bench.task_body',return_value={}), patch('bench.ledger_cost',return_value=None), patch('bench.time.sleep'):
                    service=factory.return_value.__enter__.return_value
                    service.get.side_effect=get;service.post.side_effect=post
                    if scenario=='unknown':
                        with self.assertRaises(RuntimeError):run(work,'http://isolated')
                        self.assertEqual(posts,['http://isolated/tasks','http://isolated/tasks/new/cancel'])
                        state=json.loads((work/'runs.json').read_text())
                        self.assertEqual(state[0]['stopReason'],'unknown_usage')
                        self.assertIsNone(state[0]['costUsd'])
                    else:
                        run(work,'http://isolated')
                        self.assertEqual(posts,[])


if __name__ == '__main__':
    unittest.main()
