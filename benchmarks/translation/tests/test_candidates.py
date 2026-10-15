import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchmarks.translation import candidates, report, review, runner
from benchmarks.translation.common import DIMENSIONS, digest, read, write


class CandidateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.data = self.root / 'data'
        self.data.mkdir()
        self.sample = {'id': 'a', 'source_text': 'then break procedure merge(other)',
                       'selection_context': 'Algorithm listing', 'selection_glossary': [],
                       'domain': 'algorithms', 'origin': 'real', 'split': 'development', 'length': 'short'}
        (self.data / 'samples.jsonl').write_text(json.dumps(self.sample) + '\n')
        write(self.data / 'sources.json', [])
        write(self.data / 'rubric.json', {'version': 'test', 'dimensions': DIMENSIONS})
        self.recipe = {'schema': 1, 'version': 'test-1', 'cases': [{
            'id': 'boundary', 'source': {'sample_id': 'a', 'sha256': digest(self.sample['source_text'].encode())},
            'review_points': ['break and procedure are separate statements'],
            'variants': [{'id': 'original'}, {'id': 'linebreak', 'line_break_before': ['procedure merge(other)']}]}]}
        self.path = self.root / 'recipe.json'
        write(self.path, self.recipe)
        self.config = {'provider': 'openai', 'model': 'test', 'base_url': 'https://test.invalid/v1',
                       'limits': {'max_requests': 2, 'max_seconds': 10}}
        self.calls = []
        parent = self
        class Product:
            def generate_sample(self, sample, timeout):
                parent.calls.append(sample)
                return {'status': 'ok', 'translation': '中断\n过程 merge(other)', 'usage': {'availability': 'unavailable'},
                        'elapsed_seconds': 1, 'ttft_seconds': .5}
            def close(self):
                pass
        self.enterContext(patch('benchmarks.translation.product.ProductService', return_value=Product()))
        self.enterContext(patch.object(runner, 'verify', return_value={'dataset': 'frozen', 'content_sha256': 'frozen'}))
        self.enterContext(patch.object(runner, 'provider_identity', side_effect=lambda c: c))
        self.enterContext(patch.object(runner, 'code_identity', return_value={'commit': 'test'}))

    def execute(self, output):
        return runner.run(self.data, self.config, 'selection', output, candidates=self.path)

    def test_variants_preserve_frozen_source_and_share_evidence_group(self):
        original = (self.data / 'samples.jsonl').read_bytes()
        pack = candidates.load(self.data, self.path)
        self.assertEqual((self.data / 'samples.jsonl').read_bytes(), original)
        self.assertEqual(pack['groups'], 1)
        self.assertEqual(len({s['group'] for s in pack['samples']}), 1)
        self.assertEqual(pack['samples'][1]['source_text'], 'then break \nprocedure merge(other)')
        self.assertEqual(pack['samples'][0]['selection_context'], 'Algorithm listing')
        self.recipe['cases'][0]['source']['sha256'] = 'wrong'
        write(self.path, self.recipe)
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            candidates.load(self.data, self.path)

    def test_duplicate_variants_and_silent_recipe_typos_are_rejected(self):
        for variant in ({'id': 'same'}, {'id': 'typo', 'glosary_additions': []},
                        {'id': 'missing', 'line_break_before': ['absent']}):
            recipe = copy.deepcopy(self.recipe)
            recipe['cases'][0]['variants'][1] = variant
            write(self.path, recipe)
            with self.assertRaises(ValueError):
                candidates.load(self.data, self.path)

    def test_preflight_and_resume_do_not_repeat_calls_and_annotation_drift_is_rejected(self):
        plan = runner.preflight(self.data, self.config, 'selection', self.path)
        self.assertEqual(plan['planned_requests'], 2)
        self.assertEqual(self.calls, [])
        out = self.root / 'run'
        self.execute(out)
        runner.run(self.data, self.config, 'selection', out, candidates=self.path, resume=True)
        self.assertEqual(len(self.calls), 2)
        self.recipe['cases'][0]['review_points'].append('new scoring interpretation')
        write(self.path, self.recipe)
        with self.assertRaisesRegex(ValueError, 'changed'):
            runner.run(self.data, self.config, 'selection', out, candidates=self.path, resume=True)
        self.assertEqual(len(self.calls), 2)

    def test_candidate_results_are_labeled_and_cannot_be_compared_to_frozen_scores_or_promoted(self):
        out = self.root / 'run'
        self.execute(out)
        page = Path(report.report([out], self.root / 'report')).read_text()
        self.assertIn('候选诊断（不计入冻结基线）', page)
        self.assertEqual(report.summarize(out)['correlation_groups'], 1)
        packet = self.root / 'packet'
        review.prepare([out], packet)
        entries = read(packet / 'packet.json')['items']
        self.assertTrue(all(e['candidate_review_points'] and e['selection_context'] == 'Algorithm listing' for e in entries))
        with self.assertRaisesRegex(ValueError, 'Candidate diagnostics'):
            review.promote(out, self.root / 'registry', 'bad')
        other = self.root / 'frozen'
        shutil.copytree(out, other)
        manifest = read(other / 'run.json')
        del manifest['identity']['candidates']
        write(other / 'run.json', manifest)
        self.assertFalse(report.compare([out], [other])['quality_comparable'])

    def test_stability_accepts_three_distinct_runs_but_rejects_drift_or_copied_ids(self):
        paths = [self.root / str(i) for i in range(3)]
        for path in paths:
            self.execute(path)
        value = report.stability(paths)
        self.assertEqual(value['runs'], 3)
        self.assertFalse(value['performance_claim_allowed'])
        self.assertEqual(value['metrics']['latency_p50']['sample_stdev'], 0)
        with self.assertRaises(ValueError):
            report.stability(paths[:2] + [paths[0]])
        manifest = read(paths[2] / 'run.json')
        manifest['identity']['code']['commit'] = 'changed'
        manifest['fingerprint'] = digest(manifest['identity'])
        write(paths[2] / 'run.json', manifest)
        with self.assertRaisesRegex(ValueError, 'identical'):
            report.stability(paths)
