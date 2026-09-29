import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
from review import validate_review, audit_selection, revision_summary
from bench import RUBRIC


class ReviewTests(unittest.TestCase):
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
