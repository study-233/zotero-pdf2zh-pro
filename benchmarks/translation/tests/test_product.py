"""No live models: transport faults, cache provenance and review lifecycle."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from benchmarks.translation import product, providers, review, pdf_review, report
from benchmarks.translation.common import DIMENSIONS, append, digest, file_hash, read, write


class ProductBoundaryTests(unittest.TestCase):
    def service(self, handler, events=()):
        service = product.ProductService.__new__(product.ProductService)
        service.config, service.api, service.service = {}, {}, 'openai'
        service.url = 'http://benchmark.invalid'
        service.client = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(service.client.close)
        snapshots = iter([[], list(events)])
        service.events = lambda: next(snapshots)
        return service

    def test_selection_uses_context_glossary_and_real_route(self):
        requests = []
        def handler(req):
            requests.append(req)
            return httpx.Response(200, text='event: done\ndata: '+json.dumps({'translation':'译文','cached':True,'provider':'memory'})+'\n\n')
        service = self.service(handler)
        result = service.generate_sample({'id':'case','source_text':'Only selected text.','selection_context':'Surrounding context.', 'selection_glossary':[{'source':'lock','target':'锁'}]}, 2)
        body = json.loads(requests[0].content)
        self.assertEqual(requests[0].url.path, '/translate-text/stream')
        self.assertEqual(body['context'], 'Surrounding context.')
        self.assertEqual(body['glossaryEntries'][0]['target'], '锁')
        self.assertEqual((result['usage']['input'], result['usage']['calls']), (0,0))
        self.assertEqual(result['cache_source'], 'memory')

    def test_selection_disconnect_cancels_once_without_resubmission(self):
        routes = []
        def handler(req):
            routes.append(req.url.path)
            if req.url.path.endswith('/stream'):
                raise httpx.ReadTimeout('simulated disconnect')
            return httpx.Response(200, json={'cancelled':True})
        result = self.service(handler).generate_sample({'id':'case','source_text':'text'},2)
        self.assertEqual(routes, ['/translate-text/stream','/cancel-text'])
        self.assertEqual(result['status'], 'error')
        self.assertIsNone(result['usage']['input'])

    def test_reconciliation_finds_task_after_lost_submission_and_rejects_duplicates(self):
        tasks = [{'taskId':'owned', 'fileName':'bench-unique.pdf'}]
        service = self.service(lambda req:httpx.Response(200,json={'tasks':tasks}))
        self.assertEqual(service.reconcile('bench-unique.pdf')['taskId'],'owned')
        tasks.append(dict(tasks[0]))
        with self.assertRaisesRegex(ValueError,'Ambiguous'):
            service.reconcile('bench-unique.pdf')

    def test_missing_journal_is_unknown_unless_zero_calls_are_established(self):
        self.assertIsNone(product.ledger_usage([],3)['input'])
        self.assertIsNone(product.ledger_usage([])['input'])
        self.assertEqual(product.ledger_usage([],0)['availability'],'complete')

    def test_warm_seed_requires_matching_completed_run_and_copies_only_caches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); seed=root/'seed'; config={'provider':'openai','model':'x','cache':'warm','cache_seed':str(seed)}
            with self.assertRaises(ValueError): providers.cache_seed_identity(config)
            write(seed/'run.json',{'run_id':'original','status':'completed','identity':{'suite':'selection','dataset_sha256':'d','provider':{'provider':'openai','model':'x'}}})
            path=seed/'runtime'/'tasks'/'selection-cache.sqlite3';path.parent.mkdir(parents=True);path.write_bytes(b'closed fixture')
            (path.parent/'tasks.json').write_text('do not copy old task IDs')
            identity=providers.cache_seed_identity(config)
            destination=root/'new';product.seed_cache(config,destination)
            self.assertEqual((destination/'tasks'/'selection-cache.sqlite3').read_bytes(),b'closed fixture')
            self.assertFalse((destination/'tasks'/'tasks.json').exists())
            self.assertEqual(identity['run_id'],'original')
            with self.assertRaises(ValueError):providers.cache_seed_identity({**config,'model':'other'})

    def test_lmstudio_only_unloads_owned_instance(self):
        config={'model':'fixture','base_url':'http://localhost:1234/v1','context_length':8192,'lms_path':'fake-lms'}
        with patch('subprocess.check_output',side_effect=['[]','[{"identifier":"fixture"}]']),patch('subprocess.run') as call:
            adapter=providers.LMStudioProvider(config)
            owned=adapter.owned_model
            adapter.close()
            commands=[c.args[0] for c in call.call_args_list]
        self.assertIn(owned,commands[0]); self.assertEqual(commands[-1],['fake-lms','unload',owned])
        self.assertFalse(any('--all' in command for command in commands))

    def test_chatgpt_warm_seed_accepts_its_fixed_implicit_responses_protocol(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root/'run.json', {'run_id':'chatgpt-seed','status':'completed','identity':{
                'suite':'pdf-quick','dataset_sha256':'data','provider':{
                    'provider':'chatgpt','model':'m','protocol':'responses'}}})
            cache = root/'runtime/cache/translations.db'
            cache.parent.mkdir(parents=True); cache.write_bytes(b'closed fixture')
            config = {'provider':'chatgpt','model':'m','cache':'warm','cache_seed':str(root)}
            self.assertEqual(providers.cache_seed_identity(config)['run_id'], 'chatgpt-seed')
            with self.assertRaisesRegex(ValueError, 'protocol'):
                providers.cache_seed_identity({**config,'protocol':'chat_completions'})


class PDFReviewTests(unittest.TestCase):
    def test_page_packet_is_blind_and_review_is_versioned(self):
        import fitz
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);run=root/'run';run.mkdir()
            for name in ('source','mono','dual'):
                with fitz.open() as doc:
                    page=doc.new_page();page.insert_text((72,72),'Only committed entries are visible.')
                    doc.save(run/(name+'.pdf'))
            row={'job_id':'main:paper:0','sample_id':'paper','phase':'main','status':'ok','translation':'','input_pdf':'source.pdf','artifacts':[{'mode':mode,'path':mode+'.pdf','sha256':file_hash(run/(mode+'.pdf'))} for mode in ('mono','dual')],'usage':{},'elapsed_seconds':1,'rules':{}}
            append(run/'results.jsonl',row)
            write(run/'samples.json',{'paper':{'source_text':'Source title','anchors':[{'page':1,'role':'quick'}]}})
            write(run/'rubric.json',{'version':'test'})
            packet=root/'packet';pdf_review.prepare(run,packet)
            payload=read(packet/'packet.json')
            self.assertNotIn('job_id',json.dumps(payload));self.assertNotIn(str(run),json.dumps(payload))
            self.assertTrue((packet/payload['items'][0]['dual_images'][0]).exists())
            verdict=read(packet/'review-template.json');verdict.update(reviewer='human',reviewer_version='v1')
            verdict['items'][0].update(scores=dict.fromkeys(DIMENSIONS,4),layout_readability=4,content_completeness=4,layout_evidence='Both mono and bilingual images inspected.',content_evidence='All lines present.')
            write(packet/'verdict.json',verdict);pdf_review.import_reviews(packet,packet/'verdict.json')
            self.assertEqual(read(run/'reviews.json')['main:paper:0']['anchors_reviewed'],1)
            review.new_round(run,'round-1')
            self.assertFalse((run/'reviews.json').exists())
            self.assertTrue((run/'review-history'/'round-1'/'pdf-review.json').exists())

    def test_pdf_failure_is_execution_failure_not_zero_translation_quality(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); row={'job_id':'main:p:0','sample_id':'p','phase':'main','status':'error','translation':'','usage':{}}
            append(root/'results.jsonl',row)
            write(root/'run.json',{'run_id':'test','status':'completed','planned_jobs':['main:p:0'],'identity':{'suite':'pdf-full','dataset_sha256':'x','provider':{'model':'test'}}})
            write(root/'samples.json',{'p':{'source_text':'Paper','group':'p'}})
            summary=report.summarize(root)
            self.assertEqual(summary['success_rate'],0)
            self.assertIsNone(summary['quality_score'])
            self.assertIsNone(summary['pdf_quality']['layout_readability'])
