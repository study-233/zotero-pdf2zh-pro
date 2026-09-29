import copy
import sys
import unittest
import tempfile
import json
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).parent))
from review import validate_review, audit_selection, revision_summary, recorded_cost, export_pdf_manifest
from bench import RUBRIC, digest, write, read


class ReviewTests(unittest.TestCase):
    def test_pdf_export_preserves_bytes_and_disclosure_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);work=root/'work';(work/'outputs').mkdir(parents=True)
            source=work/'outputs'/'original.pdf';source.write_bytes(b'%PDF-original')
            snapshot={'papers':[{'id':'paper','pages':2,'redistributionApproved':False}]}
            runs=[{'taskId':'original','paper':'paper','model':'model','status':'incomplete','outputSha256':digest(source)}]
            public=root/'public';private=root/'private'
            export_pdf_manifest(work,public,snapshot,runs)
            self.assertEqual(read(public/'pdfs.json')['documents'][0]['availability'],'local_only')
            self.assertFalse((public/'pdfs').exists())
            with patch('review.DEFAULT_WORK',root/'default'):
                export_pdf_manifest(work,private,snapshot,runs,True)
            self.assertEqual((private/'pdfs'/'original.pdf').read_bytes(),source.read_bytes())
            source.write_bytes(b'changed')
            with self.assertRaises(ValueError):export_pdf_manifest(work,public,snapshot,runs)

    def test_supplement_pdf_never_claims_original_review(self):
        with tempfile.TemporaryDirectory() as directory:
            work=Path(directory);(work/'outputs').mkdir()
            source=work/'outputs'/'new.pdf';source.write_bytes(b'%PDF-new')
            runs=[{'taskId':'old','paper':'paper','model':'model','status':'failed'}]
            supplement={'taskId':'new','paper':'paper','model':'model','status':'completed',
                        'supplementalTo':'old','reviewStatus':'not_reviewed','outputSha256':digest(source),
                        'apiKey':'DO-NOT-EXPORT'}
            write(work/'supplemental-runs.json',[supplement])
            export_pdf_manifest(work,work/'site',{'papers':[{'id':'paper','pages':2}]},runs)
            manifest=read(work/'site'/'pdfs.json');doc=manifest['documents'][0]
            self.assertEqual(doc['taskId'],'new');self.assertTrue(doc['supplemental'])
            self.assertEqual(doc['supplementaryRun']['reviewStatus'],'not_reviewed')
            self.assertNotIn('DO-NOT-EXPORT',json.dumps(manifest))

    def test_recorded_cost_exposes_partial_usage_without_filling_missing_cost(self):
        with tempfile.TemporaryDirectory() as directory:
            work=Path(directory); (work/'usage').mkdir()
            model={'input':1,'output':2,'cacheRead':.1}
            events=[{'input':1000,'output':500,'hit':800,'timestamp':0},
                    {'input':None,'output':None,'timestamp':0}]
            (work/'usage'/'task.jsonl').write_text('\n'.join(json.dumps(e) for e in events),encoding='utf-8')
            result=recorded_cost(work,'task',model)
            self.assertAlmostEqual(result['recordedCostUsd'],.00128)
            self.assertEqual(result['usageKnownRequests'],1)
            self.assertEqual(result['usageMissingRequests'],1)
            self.assertNotIn('costUsd',result)
            (work/'usage'/'task.jsonl').write_text(json.dumps(events[1]),encoding='utf-8')
            self.assertIsNone(recorded_cost(work,'task',model)['recordedCostUsd'])
            self.assertIsNone(recorded_cost(work,'absent',model)['recordedCostUsd'])

    def setUp(self):
        self.packet={'paper':'test','samples':[{'id':f'S{i:02}'} for i in range(12)],'candidates':[{'alias':a} for a in 'ABCDE']}
        self.review={'paper':'test','judge':'gpt-6-sol','candidates':[
            {'alias':a,'samples':[{'id':s['id'],'scores':dict(RUBRIC),'issues':[]} for s in self.packet['samples']]} for a in 'ABCDE']}

    def test_all_candidates_all_samples_and_correct_judge_required(self):
        validate_review(self.packet,self.review)
        for change in ('judge','candidate','sample'):
            r=copy.deepcopy(self.review)
            if change=='judge':r['judge']='other'
            if change=='candidate':r['candidates'].pop()
            if change=='sample':r['candidates'][0]['samples'].pop()
            with self.assertRaises(ValueError):validate_review(self.packet,r)

    def test_deductions_need_evidence_and_scores_must_be_finite(self):
        sample=self.review['candidates'][0]['samples'][0]
        sample['scores']['fidelity']=39
        with self.assertRaises(ValueError):validate_review(self.packet,self.review)
        sample['scores']['fidelity']=float('nan')
        with self.assertRaises(ValueError):validate_review(self.packet,self.review)

    def test_audit_is_reproducible_and_always_covers_major_issues(self):
        sample=self.review['candidates'][0]['samples'][0]
        sample['issues']=[{'severity':'major'}]
        audit=audit_selection(self.packet,self.review)
        self.assertEqual(audit,audit_selection(self.packet,self.review))
        self.assertGreaterEqual(len(audit),12)
        self.assertTrue(any(x['alias']=='A' and x['sample']=='S00' for x in audit))

    def test_public_revisions_do_not_embed_raw_evidence(self):
        value={'scores':dict(RUBRIC),'issues':[{'explanation':'summary','sourceEvidence':'PRIVATE SOURCE','translationEvidence':'PRIVATE TRANSLATION'}]}
        summary=revision_summary(value)
        self.assertIn('100/100',summary)
        self.assertIn('summary',summary)
        self.assertNotIn('PRIVATE',summary)
        self.assertIsInstance(revision_summary(None),str)


if __name__=='__main__':unittest.main()
