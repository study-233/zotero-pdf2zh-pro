import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchmarks.translation import maintenance, review
from benchmarks.translation.common import DIMENSIONS, HERE, digest, file_hash, read, write


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.records = self.root / 'records'
        self.run = self.root / 'runs/test'
        self.enterContext(patch.object(maintenance, 'ROOT', self.root))
        self.frozen = self.root / 'benchmarks/translation/datasets/cs-en-zh-v2'
        write(self.frozen / 'recipes.json', [{'id': 'p-01', 'paper': 'p', 'source_locations': [{'page': 3}]}])
        write(self.frozen / 'sources.json', [{'id': 'p', 'split': 'holdout', 'sha256': 'a'*64}])
        self.row = dict(job_id='main:p-01:0', sample_id='p-01', phase='main', status='ok', translation='译文')
        self.meta = dict(run_id='test', status='completed', planned_jobs=['main:p-01:0'], identity={
            'suite': 'quick', 'provider': {'model': 'test'}, 'dataset_sha256': 'a'*64})
        write(self.run / 'run.json', self.meta)
        (self.run / 'results.jsonl').write_text(json.dumps(self.row)+'\n')
        write(self.run / 'samples.json', {'p-01': {'source_text': 'Source', 'group': 'p'}})
        self.reviews = {'main:p-01:0': dict(result_sha256=digest(self.row), scores={d:4 for d in DIMENSIONS},
            score=100.0, errors=[], independent=False, reviewer='test')}
        write(self.run / 'reviews.json', self.reviews)
        a = review.audit(self.run)
        write(self.run / 'audit.json', {**a, 'reviewer':'test', 'independent':False,
            'verdicts': {'main:p-01:0': {'status':'confirmed', 'note':'Checked source and target.'}}})
        self.inv = [dict(run='runs/test', run_id='test', role='quality_run', suite='quick',
            execution_status='completed', main_results=1, reviewed_outputs=1, audited_outputs=1,
            no_output_results=0, unexecuted_jobs=0, unreviewed_outputs=0, files={})]
        self.ref = dict(run='runs/test', job_id=self.row['job_id'], result_sha256=digest(self.row))
        write(self.records / 'exposure.json', {'holdout_exposures':[dict(paper='p', source_sha256='a'*64,
            original_split_unchanged=True, pages=[3], paper_level_affected_sample_ids=['p-01'],
            direct_overlap_sample_ids=['p-01'], evidence=[self.ref])]})
        write(self.records / 'candidates.json', {'cases':[dict(id='one', title='One', status='candidate',
            lane='translation', expected='Preserve meaning', correlation_group='p', evidence=[self.ref])]})
        write(self.records / 'frozen.json', {str(f.relative_to(self.root)):file_hash(f) for f in self.frozen.iterdir()})
        write(self.records / 'calibration.json', {})
        write(self.records / 'preservation.json', {'archived_originals':[]})
        write(self.records / 'visual-check.json', {'artifacts':{}})
        self.reseal()

    def reseal(self):
        self.inv[0]['files'] = {f.name:file_hash(f) for f in self.run.iterdir() if f.is_file()}
        write(self.records / 'inventory.json', self.inv)
        write(self.records / 'manifest.json', dict(schema=1, version='test', status='engineering-review-only',
            live_calls=0, baseline_promoted=False,
            files={f.name:file_hash(f) for f in self.records.iterdir() if f.name!='manifest.json'}))

    def test_published_records_validate_without_private_outputs(self):
        with patch.object(maintenance, 'ROOT', HERE.parents[1]):
            for folder in (HERE / 'maintenance').iterdir():
                result = maintenance.verify(folder)
                self.assertFalse(result['evidence_verified'])
                self.assertEqual(result['live_calls'], 0)

    def test_offline_verification_and_report_do_not_mutate_evidence(self):
        before = {f.name:f.read_bytes() for f in self.run.iterdir()}
        with patch('socket.create_connection', side_effect=AssertionError('Network forbidden')):
            result = maintenance.report(self.records, self.root, self.root/'report')
        self.assertEqual(result['reviewed_outputs'], 1)
        self.assertEqual(before, {f.name:f.read_bytes() for f in self.run.iterdir()})
        self.assertTrue((self.root/'report/details/index.html').exists())
        self.assertIn('details/index.html', (self.root/'report/index.html').read_text())
        with self.assertRaisesRegex(ValueError, 'already exists'):
            maintenance.report(self.records, self.root, self.root/'report')

    def test_portable_evidence_root(self):
        other = self.root/'relocated'
        shutil.copytree(self.run.parent, other/'runs')
        self.assertTrue(maintenance.verify(self.records, other)['evidence_verified'])

    def test_raw_result_drift_rejected(self):
        (self.run/'results.jsonl').write_text('{}\n')
        with self.assertRaisesRegex(ValueError, 'Hash mismatch'):
            maintenance.verify(self.records, self.root)

    def test_stale_audit_rejected_even_after_manifest_updated(self):
        a=read(self.run/'audit.json'); a['review_sha256']='b'*64;write(self.run/'audit.json',a);self.reseal()
        with self.assertRaisesRegex(ValueError, 'Stale audit'):
            maintenance.verify(self.records, self.root)

    def test_missing_audit_item_rejected(self):
        a=read(self.run/'audit.json'); a['verdicts']={};write(self.run/'audit.json',a);self.reseal()
        with self.assertRaisesRegex(ValueError, 'Audit coverage'):
            maintenance.verify(self.records, self.root)

    def test_bad_score_rejected(self):
        self.reviews['main:p-01:0']['score']=90;write(self.run/'reviews.json',self.reviews);self.reseal()
        with self.assertRaisesRegex(ValueError, 'Weighted score'):
            maintenance.verify(self.records, self.root)

    def test_incomplete_exposure_rejected(self):
        e=read(self.records/'exposure.json');e['holdout_exposures'][0]['paper_level_affected_sample_ids']=[]
        write(self.records/'exposure.json',e);self.reseal()
        with self.assertRaisesRegex(ValueError, 'paper-level exposure'):
            maintenance.verify(self.records)

    def test_candidate_evidence_drift_rejected(self):
        c=read(self.records/'candidates.json');c['cases'][0]['evidence'][0]['result_sha256']='b'*64
        write(self.records/'candidates.json',c);self.reseal()
        with self.assertRaisesRegex(ValueError, 'result drift'):
            maintenance.verify(self.records, self.root)

    def test_absolute_and_parent_paths_rejected_offline(self):
        for name in ('/private/test', '../escape'):
            self.inv[0]['run']=name;self.reseal()
            with self.assertRaisesRegex(ValueError, 'inside'):
                maintenance.verify(self.records)

    def test_frozen_input_drift_rejected(self):
        write(self.frozen/'recipes.json',[])
        with self.assertRaisesRegex(ValueError, 'Hash mismatch'):
            maintenance.verify(self.records)

    def test_no_pdf_output_is_not_unreviewed_or_zero_quality(self):
        self.meta['identity']['suite']='pdf-quick';write(self.run/'run.json',self.meta)
        row={**self.row,'translation':'','status':'error'}
        (self.run/'results.jsonl').write_text(json.dumps(row)+'\n')
        (self.run/'reviews.json').unlink();(self.run/'audit.json').unlink()
        self.inv[0].update(suite='pdf-quick',reviewed_outputs=0,audited_outputs=0,no_output_results=1)
        for name,key in [('candidates.json','cases'),('exposure.json','holdout_exposures')]:write(self.records/name,{key:[]})
        self.reseal()
        maintenance.report(self.records,self.root,self.root/'report')
        r=read(self.root/'report/summary.json')['runs'][0]
        self.assertIsNone(r['quality_score']);self.assertEqual(r['unreviewed'],0)
        self.assertEqual(r['review_status'],'no-output-not-applicable');self.assertEqual(r['success_rate'],0)

    def test_partial_run_keeps_subset_score_but_no_complete_score(self):
        self.meta.update(status='stopped_after_pdf_error',planned_jobs=['main:p-01:0','main:p-02:0'])
        write(self.run/'run.json',self.meta)
        self.inv[0]['unexecuted_jobs']=1;self.reseal()
        maintenance.report(self.records,self.root,self.root/'report')
        r=read(self.root/'report/summary.json')['runs'][0]
        self.assertIsNone(r['quality_score']);self.assertEqual(r['reviewed_subset_score'],100)
        self.assertEqual(r['unexecuted_jobs'],1);self.assertFalse(r['complete'])
