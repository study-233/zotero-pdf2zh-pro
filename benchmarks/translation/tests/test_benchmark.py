import copy
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from benchmarks.translation import corpus, providers, report, review, runner
from benchmarks.translation.__main__ import main
from benchmarks.translation.common import DATASET, DIMENSIONS, HERE, digest, read, rows, write
from benchmarks.translation.product import ProductService, ledger_usage


class DatasetTests(unittest.TestCase):
    def test_public_recipe_pack_covers_all_quotas_without_pdfs_or_network(self):
        result = corpus.verify(HERE / 'datasets' / DATASET, metadata_only=True)
        self.assertEqual((result['samples'], result['pdfs'], result['anchors']), (240, 16, 48))

    def test_quick_covers_domains_challenges_and_length_strata(self):
        root = HERE / 'datasets' / DATASET
        samples = {s['id']: s for s in read(root / 'recipes.json') + rows(root / 'challenges.jsonl')}
        quick = [samples[sid] for sid in read(root / 'manifest.json')['quick_ids']]
        self.assertEqual(len({s['domain'] for s in quick}), 8)
        self.assertEqual(len({s['length'] for s in quick}), 4)
        self.assertEqual(len({s['tags'][1] for s in quick if s['origin'] == 'challenge'}), 10)
        self.assertTrue(all(s['split'] == 'development' for s in quick))

    def test_formal_schedule_has_exactly_283_unique_jobs(self):
        root = HERE / 'datasets' / DATASET
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            write(p / 'manifest.json', read(root / 'manifest.json'))
            (p / 'samples.jsonl').write_text('\n'.join(json.dumps(s) for s in read(root / 'recipes.json') + rows(root / 'challenges.jsonl')))
            jobs = runner.jobs_for(p, 'model')
        self.assertEqual(len(jobs), 283)
        self.assertEqual(len({j['job_id'] for j in jobs}), 283)
        self.assertEqual(sum(j['phase'] == 'repeat' for j in jobs), 40)

    def test_zip_traversal_is_rejected_without_writing_outside_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / 'bad.zip'
            with zipfile.ZipFile(archive, 'w') as package:
                package.writestr('../escape', 'bad')
            with self.assertRaises(ValueError):
                corpus.import_package(archive, root / 'imported')
            self.assertFalse((root / 'escape').exists())
            self.assertFalse((root / 'imported').exists())

    def test_duplicate_zip_entries_are_rejected(self):
        import warnings
        with tempfile.TemporaryDirectory() as tmp, warnings.catch_warnings():
            warnings.simplefilter('ignore')
            root = Path(tmp)
            with zipfile.ZipFile(root / 'bad.zip', 'w') as package:
                package.writestr('x', '1')
                package.writestr('x', '2')
            with self.assertRaises(ValueError):
                corpus.import_package(root / 'bad.zip', root / 'imported')

    def test_immutable_source_and_template_splits(self):
        root = HERE / 'datasets' / DATASET
        sources = read(root / 'sources.json')
        for domain in {s['domain'] for s in sources}:
            pair = [s for s in sources if s['domain'] == domain]
            self.assertEqual({s['split'] for s in pair}, {'development', 'holdout'})
            self.assertNotEqual(pair[0]['sha256'], pair[1]['sha256'])
        families = {}
        for case in rows(root / 'challenges.jsonl'):
            self.assertEqual(families.setdefault(case['template_family'], case['split']), case['split'])


class ProviderTests(unittest.TestCase):
    def invoke(self, response_bytes, *, protocol='chat_completions', status=200):
        import httpx
        requests = []
        def handler(request):
            requests.append(json.loads(request.content))
            return httpx.Response(status, content=response_bytes, headers={'content-type': 'text/event-stream'})
        client = httpx.Client(transport=httpx.MockTransport(handler))
        config = {'provider': 'openai', 'model': 'test-model', 'base_url': 'https://test.invalid/v1', 'protocol': protocol,
                  'parameters': {'max_tokens': 100, 'temperature': 0}, 'limits': {'max_requests': 1, 'max_seconds': 10}}
        with patch('httpx.Client', return_value=client):
            provider = providers.OpenAIProvider(config)
        try:
            result = provider.generate('same instruction', 10)
        finally:
            provider.close()
        return result, requests

    def test_stream_usage_snapshots_are_replaced_and_empty_choice_is_valid(self):
        events = [{'choices': [{'delta': {'content': '译文'}, 'index': 0}]},
                  {'choices': [{'delta': {}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 2}},
                  {'choices': [], 'usage': {'prompt_tokens': 10, 'completion_tokens': 2}}]
        value, requests = self.invoke(''.join('data: '+json.dumps(e)+'\n\n' for e in events).encode())
        self.assertEqual(value['translation'], '译文')
        self.assertEqual(value['status'], 'ok')
        self.assertEqual(value['usage']['input'], 10)
        self.assertEqual(requests[0]['messages'][0]['content'], 'same instruction')
        self.assertIsNotNone(value['ttft_seconds'])

    def test_truncation_retains_output_and_usage_but_is_not_success(self):
        event = {'choices': [{'delta': {'content': '部分'}, 'finish_reason': 'length'}], 'usage': {'prompt_tokens': 8, 'completion_tokens': 1}}
        value, _ = self.invoke(('data: '+json.dumps(event)+'\n\n').encode())
        self.assertEqual((value['status'], value['translation']), ('error', '部分'))
        self.assertEqual(value['usage']['output'], 1)

    def test_http_error_is_not_retried_or_logged_with_its_secret_body(self):
        value, requests = self.invoke(b'{"error":"secret-key-body"}', status=429)
        self.assertEqual(len(requests), 1)
        self.assertEqual(value['error']['status_code'], 429)
        self.assertNotIn('secret-key-body', json.dumps(value))

    def test_responses_stream_requires_terminal_completed_event(self):
        events = [{'type': 'response.output_text.delta', 'delta': '答案'}, {'type': 'response.completed', 'response': {'status': 'completed', 'usage': {'input_tokens': 7, 'output_tokens': 2}}}]
        value, request = self.invoke(''.join('data: '+json.dumps(e)+'\n\n' for e in events).encode(), protocol='responses')
        self.assertEqual(value['status'], 'ok')
        self.assertEqual(request[0]['max_output_tokens'], 100)
        self.assertNotIn('max_tokens', request[0])

    def test_raw_completions_preserve_same_task_instruction(self):
        event = {'choices': [{'text': '输出', 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 7, 'completion_tokens': 2}}
        result, request = self.invoke(('data: '+json.dumps(event)+'\n\n').encode(), protocol='completions')
        self.assertEqual(request[0]['prompt'], 'same instruction')
        self.assertEqual(result['translation'], '输出')

    def test_anthropic_stream_merges_cumulative_usage_and_counts_cache_tokens(self):
        events = [
            {'type': 'message_start', 'message': {'id': 'msg-test', 'model': 'actual-model', 'usage': {
                'input_tokens': 10, 'output_tokens': 1, 'cache_read_input_tokens': 20, 'cache_creation_input_tokens': 5}}},
            {'type': 'content_block_delta', 'delta': {'type': 'thinking_delta', 'thinking': 'hidden'}},
            {'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': '译文'}},
            {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn'}, 'usage': {'output_tokens': 3}},
            {'type': 'message_delta', 'delta': {}, 'usage': {'output_tokens': 3}},
            {'type': 'message_stop'},
        ]
        result, requests = self.invoke(''.join('data: '+json.dumps(e)+'\n\n' for e in events).encode(), protocol='anthropic_messages')
        self.assertEqual((result['status'], result['translation']), ('ok', '译文'))
        self.assertEqual((result['usage']['input'], result['usage']['output'], result['usage']['cached_input'], result['usage']['cache_write_input']), (35, 3, 20, 5))
        self.assertEqual((result['response_id'], result['response_model']), ('msg-test', 'actual-model'))
        self.assertNotIn('stream_options', requests[0])
        rates = {'input': 1, 'output': 2, 'cached_input': .1, 'cache_write': 1.25}
        self.assertAlmostEqual(runner.estimate_cost(result, rates), 24.25 / 1_000_000)
        self.assertIsNone(runner.estimate_cost(result, {k: v for k, v in rates.items() if k != 'cache_write'}))

    def test_anthropic_stream_requires_message_stop_even_after_end_turn(self):
        events = [
            {'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': '部分译文'}},
            {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn'}, 'usage': {'output_tokens': 3}},
        ]
        result, _ = self.invoke(''.join('data: '+json.dumps(e)+'\n\n' for e in events).encode(), protocol='anthropic_messages')
        self.assertEqual((result['status'], result['translation']), ('error', '部分译文'))
        self.assertEqual(result['usage']['output'], 3)

    def test_plain_credentials_and_endpoint_credentials_are_rejected(self):
        config = {'provider': 'openai', 'model': 'x', 'base_url': 'https://test.invalid/v1', 'limits': {'max_requests': 1, 'max_seconds': 1}}
        for extra in ({'api_key': 'secret'}, {'base_url': 'https://u:p@test.invalid/v1'}, {'base_url': 'https://test.invalid/v1?key=secret'}):
            with self.subTest(extra=list(extra)), self.assertRaises(ValueError):
                providers.validate_config({**config, **extra})

    def test_call_ledger_reconciles_failed_usage_and_duplicate_events(self):
        events = [{'event': 'request_started', 'callId': 'a'}, {'event': 'usage', 'callId': 'a', 'input': 10, 'output': 2, 'complete': True}, {'event': 'request_finished', 'callId': 'a', 'succeeded': False}]
        value = ledger_usage(events + [events[1]])
        self.assertEqual((value['input'], value['calls'], value['availability']), (10, 1, 'complete'))
        self.assertEqual(ledger_usage(events[:1])['availability'], 'unavailable')

    def test_product_cache_hit_is_not_labeled_as_provider_cache_or_generation(self):
        import httpx
        captured = []
        def handle(request):
            captured.append(json.loads(request.content))
            value = {'translation': '选段译文', 'provider': 'codex', 'cached': True}
            return httpx.Response(200, content=('event: done\ndata: '+json.dumps(value)+'\n\n').encode())
        service = ProductService.__new__(ProductService)
        service.config, service.service, service.api = {'cache': 'warm'}, 'codex', {'model': 'test'}
        service.url, service.events = 'http://test.invalid', lambda: []
        sample = {'id': 'selection', 'paper': None, 'source_text': 'Selected text.', 'selection_context': 'Longer context.',
                  'selection_glossary': [{'source': 'text', 'target': '文本'}]}
        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            service.client = client
            value = service.generate_sample(sample, 1)
        self.assertEqual(value['cache_source'], 'selection-cache')
        self.assertEqual((value['usage']['calls'], value['usage']['input'], value['usage']['output']), (0, 0, 0))
        self.assertEqual(captured[0]['text'], sample['source_text'])
        self.assertEqual(captured[0]['context'], sample['selection_context'])
        self.assertEqual(captured[0]['glossaryEntries'], sample['selection_glossary'])
        self.assertEqual(captured[0]['documentFingerprint'], digest(b'selection'))


class RunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / 'data'
        self.data.mkdir()
        self.sample = {'id': 'a', 'source_text': 'Only committed entries are visible.', 'domain': 'systems', 'origin': 'real', 'length': 'short', 'split': 'development', 'group': 'a', 'must_preserve': [], 'key_points': ['必要条件']}
        (self.data / 'samples.jsonl').write_text(json.dumps(self.sample)+'\n')
        write(self.data / 'manifest.json', {'quick_ids': ['a'], 'selection_ids': ['a'], 'repeat_ids': []})
        write(self.data / 'rubric.json', {'version': 'test', 'dimensions': DIMENSIONS})
        self.config = {'provider': 'openai', 'model': 'test', 'base_url': 'https://test.invalid/v1', 'limits': {'max_requests': 10, 'max_seconds': 100}}
        self.enterContext(patch.object(runner, 'verify', return_value={'dataset': DATASET, 'content_sha256': 'frozen'}))
        self.enterContext(patch.object(runner, 'provider_identity', side_effect=lambda c: c))
        self.enterContext(patch.object(runner, 'code_identity', return_value={'commit': 'test', 'dirty_sha256': 'same'}))
        self.output = self.root / 'run'
        self.calls = []
        parent = self
        class FakeProvider:
            def generate(self, prompt, timeout):
                parent.calls.append(prompt)
                return {'status': 'ok', 'translation': '只有已提交的条目可见。', 'usage': {'input': 10, 'output': 4, 'cached_input': 0, 'reasoning': None, 'availability': 'complete'}, 'elapsed_seconds': .1, 'ttft_seconds': .01, 'error': None}
            def close(self):
                pass
        self.provider = FakeProvider()

    def run_fixture(self):
        return runner.run(self.data, self.config, 'quick', self.output, provider_factory=lambda _: self.provider)

    def test_preflight_makes_no_provider_calls_or_run_directory(self):
        result = runner.preflight(self.data, self.config, 'quick')
        self.assertEqual(result['planned_requests'], 4)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.calls, [])

    def test_completed_resume_never_repeats_generation(self):
        self.run_fixture()
        runner.run(self.data, self.config, 'quick', self.output, resume=True, provider_factory=lambda _: self.provider)
        self.assertEqual(len(self.calls), 4)

    def test_resume_refuses_model_configuration_drift(self):
        self.run_fixture()
        with self.assertRaisesRegex(ValueError, 'changed'):
            runner.run(self.data, {**self.config, 'model': 'different'}, 'quick', self.output, resume=True)

    def test_unknown_dispatched_request_is_never_automatically_resent(self):
        original = self.provider.generate
        def fail_on_main(prompt, timeout):
            if self.sample['source_text'] in prompt:
                raise RuntimeError('simulated crash')
            return original(prompt, timeout)
        self.provider.generate = fail_on_main
        with self.assertRaises(RuntimeError):
            self.run_fixture()
        with self.assertRaisesRegex(ValueError, 'unknown outcomes'):
            runner.run(self.data, self.config, 'quick', self.output, resume=True, provider_factory=lambda _: self.provider)
        self.provider.generate = original
        runner.run(self.data, self.config, 'quick', self.output, resume=True, resolve_uncertain=True, provider_factory=lambda _: self.provider)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(rows(self.output / 'results.jsonl')[-1]['status'], 'unknown')

    def test_missing_review_is_null_and_report_is_self_contained(self):
        self.run_fixture()
        value = report.summarize(self.output)
        self.assertIsNone(value['quality_score'])
        self.assertEqual(value['unreviewed'], 1)
        self.assertEqual(value['request_counts']['attempts'], 4)
        path = report.report([self.output], self.root / 'report')
        html = Path(path).read_text()
        self.assertIn('未评审', html)
        self.assertNotIn('<script src=', html)
        self.assertTrue((self.root / 'report' / 'results.csv').exists())

    def test_pdf_requests_aggregate_once_and_old_missing_repair_counts_stay_unknown(self):
        self.run_fixture()
        manifest = read(self.output / 'run.json')
        manifest['identity']['suite'] = 'pdf-quick'
        write(self.output / 'run.json', manifest)
        values = rows(self.output / 'results.jsonl')
        for row in values:
            row['metrics'] = {'requests': {'attempts': 2, 'succeeded': 2, 'failed': 0, 'cancelled': 0, 'retries': 0},
                              'tokens': {'input': 999}}
        (self.output / 'results.jsonl').write_text('\n'.join(json.dumps(v) for v in values))
        summary = report.summarize(self.output)
        self.assertEqual(summary['request_counts']['attempts'], 8)
        self.assertIsNone(summary['request_counts']['repairs'])
        self.assertEqual(summary['tokens']['input'], 40)

    def test_selection_counts_generation_not_http_cache_jobs_and_requires_complete_ledger(self):
        ledger = self.root / 'runtime/ledger'
        ledger.mkdir(parents=True)
        values = [{'call_ids': ['a'], 'usage': {'calls': 1}},
                  {'call_ids': [], 'usage': {'calls': 0}}]
        events = [{'event': 'request_started', 'callId': 'a'},
                  {'event': 'request_finished', 'callId': 'a', 'succeeded': True, 'cancelled': False}]
        log = ledger / 'selection.jsonl'
        log.write_text('\n'.join(json.dumps(e) for e in events + [events[-1]]))
        counts = report.selection_request_counts(self.root, values)
        self.assertEqual((counts['attempts'], counts['succeeded'], counts['failed']), (1, 1, 0))
        self.assertIsNone(counts['retries'])
        log.write_text(json.dumps(events[0]))
        self.assertIsNone(report.selection_request_counts(self.root, values)['attempts'])
        log.unlink()
        self.assertEqual(report.selection_request_counts(self.root, values[1:])['attempts'], 0)
        self.assertIsNone(report.selection_request_counts(self.root, values)['attempts'])
        self.assertIsNone(report.selection_request_counts(self.root, [{}])['attempts'])

    def test_review_requires_deduction_evidence_and_preserves_result_identity(self):
        self.run_fixture()
        packet = self.root / 'review'
        review.prepare([self.output], packet)
        value = read(packet / 'review-template.json')
        value.update(reviewer='human', reviewer_version='1')
        value['items'][0]['scores'] = dict.fromkeys(DIMENSIONS, 4)
        value['items'][0]['scores']['semantic_accuracy'] = 2
        write(packet / 'verdict.json', value)
        with self.assertRaisesRegex(ValueError, 'evidence'):
            review.import_reviews(packet, packet / 'verdict.json')
        value['items'][0]['scores']['semantic_accuracy'] = 4
        write(packet / 'verdict.json', value)
        review.import_reviews(packet, packet / 'verdict.json')
        self.assertEqual(report.summarize(self.output)['quality_score'], 100)
        with self.assertRaises(ValueError):
            review.import_reviews(packet, packet / 'verdict.json')

    def test_quick_or_unreviewed_run_cannot_be_promoted(self):
        self.run_fixture()
        with self.assertRaises(ValueError):
            review.promote(self.output, self.root / 'baselines', 'test')
        self.assertFalse((self.root / 'baselines').exists())

    def test_single_pair_cannot_claim_performance_improvement(self):
        self.run_fixture()
        comparison = report.compare([self.output], [self.output])
        self.assertTrue(comparison['quality_comparable'])
        self.assertFalse(comparison['performance_claim_allowed'])

    def test_three_pairs_require_identical_runtime_and_no_interrupted_runs(self):
        import shutil
        self.run_fixture()
        before, after = [], []
        for side, paths in [('before', before), ('after', after)]:
            for pair in range(3):
                target = self.root / f'{side}-{pair}'
                shutil.copytree(self.output, target)
                manifest = read(target / 'run.json')
                manifest['identity']['provider']['pair_id'] = str(pair)
                manifest['identity']['code'] = {'commit': side}
                write(target / 'run.json', manifest)
                paths.append(target)
        self.assertTrue(report.compare(before, after)['performance_claim_allowed'])
        value = read(after[0] / 'run.json'); value['resumed'] = True; write(after[0] / 'run.json', value)
        self.assertFalse(report.compare(before, after)['performance_claim_allowed'])

    def test_cost_requires_complete_usage_and_cache_prices(self):
        rates = {'input': 1, 'output': 4, 'cached_input': .1}
        self.assertIsNone(runner.estimate_cost({'usage': {'availability': 'partial', 'input': 10}}, rates))
        self.assertIsNone(runner.estimate_cost({'usage': {'availability': 'complete', 'input': 10, 'output': 4}}, rates))
        self.assertAlmostEqual(runner.estimate_cost({'usage': {'availability': 'complete', 'input': 10, 'output': 4, 'cached_input': 5}}, rates), 21.5/1_000_000)

    def test_formal_run_promotes_only_after_review_audit_and_keeps_original_baseline(self):
        # Small isolated fixture exercises the gate, never registers a real baseline.
        self.run_fixture()
        manifest = read(self.output / 'run.json')
        manifest['identity']['suite'] = 'model'
        write(self.output / 'run.json', manifest)
        packet = self.root / 'packet'
        review.prepare([self.output], packet)
        value = read(packet / 'review-template.json')
        value.update(reviewer='human', reviewer_version='test')
        value['items'][0]['scores'] = dict.fromkeys(DIMENSIONS, 4)
        write(packet / 'verdict.json', value)
        review.import_reviews(packet, packet / 'verdict.json')
        with self.assertRaisesRegex(ValueError, 'audit'):
            review.promote(self.output, self.root / 'baselines', 'fixture')
        audit = review.audit(self.output)
        audit.update(reviewer='second human', verdicts={job: {'status': 'confirmed', 'note': 'Fixture evidence checked.'} for job in audit['required_job_ids']})
        write(self.root / 'audit.json', audit)
        review.import_audit(self.output, self.root / 'audit.json')
        path = review.promote(self.output, self.root / 'baselines', 'fixture')
        self.assertEqual(read(path)['summary']['quality_score'], 100)
        with self.assertRaisesRegex(ValueError, 'immutable'):
            review.promote(self.output, self.root / 'baselines', 'fixture')


if __name__ == '__main__':
    unittest.main()
