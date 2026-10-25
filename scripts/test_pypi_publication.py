from io import BytesIO
import unittest
import subprocess
from urllib.error import HTTPError
from unittest.mock import patch

import pypi_publication as gate


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.manifest = {'version': '1.9.4', 'commit': 'a'*40, 'artifacts': {
            'zotero_pdf2zh_pro-1.9.4-py3-none-any.whl': {'size': 10, 'sha256': 'b'*64},
            'zotero_pdf2zh_pro-1.9.4.tar.gz': {'size': 20, 'sha256': 'c'*64}}}
        self.now = 0
        self.calls = []

    def sleep(self, seconds):
        self.now += seconds

    def fetch(self, url, timeout):
        self.calls.append((url, timeout))
        simple = '/simple/' in url
        rows = [{'filename': n, 'size': i['size'], 'hashes' if simple else 'digests': {'sha256': i['sha256']}}
                for n, i in self.manifest['artifacts'].items()]
        return {'files' if simple else 'urls': rows}, {}

    def run_gate(self, fetch=None, timeout=600):
        return gate.wait_for_publication(self.manifest, timeout, fetch or self.fetch,
                                         lambda: self.now, self.sleep, lambda *a, **k: None)

    def test_both_public_indexes_must_match(self):
        self.assertEqual(self.run_gate()['status'], 'passed')
        self.assertEqual(len(self.calls), 2)

    def test_404_partial_files_and_index_delay_eventually_succeed(self):
        def fetch(url, timeout):
            if self.now < 5:
                raise gate.Pending('HTTP 404')
            data, headers = self.fetch(url, timeout)
            if self.now < 15:
                data[next(iter(data))].pop()
            elif self.now < 35 and '/simple/' in url:
                raise gate.Pending('index propagation')
            return data, headers
        result = self.run_gate(fetch)
        self.assertEqual(result['attempts'], 4)
        self.assertEqual(result['elapsedSeconds'], 35)

    def test_wrong_hash_or_size_never_retries(self):
        for simple in (False, True):
            def bad(url, timeout):
                data, headers = self.fetch(url, timeout)
                if ('/simple/' in url) == simple:
                    row = data[next(iter(data))][0]
                    row['hashes' if simple else 'digests']['sha256'] = 'd'*64
                return data, headers
            with self.assertRaises(gate.PublicationConflict): self.run_gate(bad)
        data, _ = self.fetch('json', 10)
        data['urls'][0]['size'] += 1
        with self.assertRaises(gate.PublicationConflict):
            gate.inspect_files(data['urls'], gate.expected_files(self.manifest))
        self.assertEqual(self.now, 0)

    def test_total_deadline_includes_requests_and_retry_after(self):
        def unavailable(url, timeout):
            self.calls.append(timeout)
            self.now += timeout
            raise gate.Pending('HTTP 429', retry_after=120)
        with self.assertRaises(gate.Pending): self.run_gate(unavailable, timeout=23)
        self.assertEqual(self.now, 23)
        self.assertTrue(all(t <= 10 for t in self.calls))

    def test_probe_returns_pending_without_sleeping(self):
        with self.assertRaises(gate.Pending):
            self.run_gate(lambda *args: (_ for _ in ()).throw(gate.Pending('missing')), timeout=0)
        self.assertEqual(self.now, 0)

    def test_http_classification(self):
        for code in (404, 408, 429, 500, 503):
            with patch.object(gate, 'urlopen', side_effect=HTTPError('url', code, 'error', {'Retry-After': '12'}, BytesIO())):
                with self.assertRaises(gate.Pending) as error: gate.fetch('https://pypi.org/test', 1)
                self.assertEqual(error.exception.retry_after, 12)
        for code in (401, 403):
            with patch.object(gate, 'urlopen', side_effect=HTTPError('url', code, 'error', {}, BytesIO())):
                with self.assertRaises(ValueError): gate.fetch('https://pypi.org/test', 1)

    def test_request_worker_enforces_elapsed_limit_even_if_network_stalls(self):
        with patch.object(gate.subprocess,'run',side_effect=subprocess.TimeoutExpired('request',3)) as run:
            with self.assertRaises(gate.Pending): gate.bounded_fetch('https://pypi.org/test',3)
            self.assertEqual(run.call_args.kwargs['timeout'],3)


if __name__ == '__main__':
    unittest.main()
