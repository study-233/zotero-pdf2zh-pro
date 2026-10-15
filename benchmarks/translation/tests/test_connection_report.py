import tempfile
from pathlib import Path
import unittest

from benchmarks.translation.common import append, file_hash, write
from benchmarks.translation.connection_report import report, summarize


class ConnectionReportTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        manifest = {'status':'completed', 'config':{'model':'<model>'}, 'runs':[]}
        for variant in ('before','after'):
            client = self.root / f'{variant}_client.py'
            client.write_text(variant)
            manifest[variant] = {'sha256':file_hash(client)}
        for index in range(1,4):
            for cache in ('cold','warm'):
                for variant in ('before','after'):
                    name = f'round-{index}-{variant}-{cache}'
                    path = self.root / name
                    calls = int(cache == 'cold')
                    manifest['runs'].append({'round':index, 'variant':variant, 'cache':cache,
                                            'directory':name, 'calls':calls, 'ok':4})
                    write(path/'run.json', {'status':'completed'})
                    for i in range(4):
                        append(path/'results.jsonl', {
                            'sample_id':str(i), 'status':'ok', 'elapsed_seconds':10 if variant=='before' else 9,
                            'usage':{'calls':calls if i==0 else 0, 'input':5, 'output':2,
                                     'cached_input':0, 'availability':'complete'},
                            'metrics':{'requests':{'retries':0,'repairs':0},
                                       'localCache':{'hits':1-calls, 'misses':calls}},
                            'translation_summary':{'failed':0,'pending':0}})
                    if calls:
                        ledger = path/'runtime/ledger/task.jsonl'
                        append(ledger, {'event':'request_started','callId':'one'})
                        append(ledger, {'event':'request_finished','callId':'one','succeeded':True,
                                        'cancelled':False,'latencyMs':1000})
                        append(path/'runtime/connection-trace/task.jsonl',
                               {'method':'POST','body_sha256':'same','status':200,'stages':[]})
        write(self.root/'comparison.json', manifest)

    def test_descriptive_export_does_not_promote_or_invent_latency(self):
        result = report(self.root, self.root/'report')
        self.assertEqual(result['integrity_issues'], [])
        self.assertEqual(len(result['pairs']), 6)
        self.assertEqual(result['pairs'][0]['reduction_percent'], 10)
        self.assertIsNone(result['runs'][2]['request_p50_s'])
        self.assertFalse(result['baseline_promoted'])
        self.assertIn('&lt;model&gt;', (self.root/'report/index.html').read_text())

    def test_missing_ledger_or_different_payload_is_visible(self):
        ledger = self.root/'round-1-after-cold/runtime/ledger/task.jsonl'
        ledger.unlink()
        trace = self.root/'round-1-after-cold/runtime/connection-trace/task.jsonl'
        trace.write_text(trace.read_text().replace('same','different'))
        result = summarize(self.root)
        self.assertTrue(any('ledger' in issue for issue in result['integrity_issues']))
        self.assertTrue(any('payloads differ' in issue for issue in result['integrity_issues']))

    def test_unknown_tokens_are_not_replaced_with_zero(self):
        path = self.root/'round-1-after-cold/results.jsonl'
        path.write_text(path.read_text().replace('"input":5', '"input":null'))
        result = summarize(self.root)
        self.assertIsNone(result['runs'][1]['input_tokens'])


if __name__ == '__main__': unittest.main()
