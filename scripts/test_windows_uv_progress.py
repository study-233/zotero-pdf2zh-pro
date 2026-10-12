"""Real uv/ConPTY downloads against a throttled localhost wheel repository.

No product installation, registry, shortcuts, or user cache are touched.
"""
import hashlib
import http.server
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CONTROL = Path(os.environ.get('PDF2ZH_TEST_CONTROL', ROOT / '.tmp/slint-target/x86_64-pc-windows-msvc/release/zotero-pdf2zh-pro.exe'))
UV = Path(os.environ.get('PDF2ZH_TEST_UV', ROOT / '.local-dev/runtime/tools/uv.exe'))
PYTHON = Path(os.environ.get('PDF2ZH_TEST_PYTHON', ROOT / '.local-dev/runtime/python/cpython-3.13.15-windows-x86_64-none/python.exe'))
for variable, path in [('PDF2ZH_TEST_CONTROL', CONTROL), ('PDF2ZH_TEST_UV', UV), ('PDF2ZH_TEST_PYTHON', PYTHON)]:
    if variable in os.environ and not path.is_file():
        raise FileNotFoundError(f'{variable}: {path}')


def wheel(name, size):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_STORED) as archive:
        directory = f'{name}-1.0.dist-info'
        archive.writestr(f'{name}/data.bin', os.urandom(size))
        archive.writestr(f'{directory}/METADATA', f'Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n')
        archive.writestr(f'{directory}/WHEEL', 'Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
        archive.writestr(f'{directory}/RECORD', '')
    return stream.getvalue()


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith('/simple/'):
            name = self.path.strip('/').split('/')[-1].replace('-', '_')
            filename = f'{name}-1.0-py3-none-any.whl'
            data = f'<a href="/{filename}" data-dist-info-metadata="true">{filename}</a>'.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if self.path.endswith('.metadata'):
            name = self.path[1:].split('-')[0]
            data = f'Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n'.encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        data = self.server.payloads.get(self.path)
        if data is None:
            self.send_error(404)
            return
        self.send_response(200)
        if not self.server.unknown_length:
            self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'public, max-age=3600')
        self.end_headers()
        sent = 0
        try:
            for offset in range(0, len(data), 32768):
                chunk = data[offset:offset + 32768]
                self.wfile.write(chunk)
                self.wfile.flush()
                sent += len(chunk)
                self.server.download_started.set()
                time.sleep(.035)
                if self.server.interrupt and sent > 700000:
                    break
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with self.server.count_lock:
                self.server.bytes_sent += sent
            self.close_connection = True

    def log_message(self, *_):
        pass


@unittest.skipUnless(os.name == 'nt' and CONTROL.is_file() and UV.is_file(), 'Windows candidate and uv required')
class UvProgressTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='uv 进度 中文 ', dir=ROOT / '.tmp')
        self.root = Path(self.directory.name)
        (self.root / 'updates').mkdir()
        self.environment = os.environ.copy()
        self.environment['UV_CACHE_DIR'] = str(self.root / 'cache')
        self.environment['UV_PYTHON_INSTALL_DIR'] = str(self.root / 'python')
        self.environment['UV_HTTP_RETRIES'] = '0'
        self.environment['UV_NO_PROGRESS'] = '1'  # Worker must override inherited hidden progress.
        self.environment['TERM'] = 'dumb'
        self.environment['NO_PROXY'] = '127.0.0.1,localhost'
        for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
            self.environment.pop(key, None)
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.payloads = {f'/{name}-1.0-py3-none-any.whl': wheel(name, size) for name, size in [('progress_alpha', 4*1024*1024), ('progress_beta', 3*1024*1024)]}
        self.server.bytes_sent = 0
        self.server.count_lock = threading.Lock()
        self.server.interrupt = False
        self.server.unknown_length = False
        self.server.download_started = threading.Event()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.directory.cleanup()

    def invoke(self, label, version='0.12.10', powershell=False, cancel=False):
        venv = self.root / label
        subprocess.run([str(UV), 'venv', '--python', str(PYTHON), '--no-config', str(venv)], env=self.environment, capture_output=True, check=True, timeout=30)
        args = ['pip', 'install', '--python', str(venv / 'Scripts/python.exe'), '--no-deps', '--no-config',
                '--index-url', f'http://127.0.0.1:{self.server.server_port}/simple', 'progress-alpha==1.0', 'progress-beta==1.0']
        request = self.root / 'request.json'
        request.write_text(json.dumps(dict(executable=str(UV), arguments=args, root=str(self.root), uvVersion=version)), encoding='utf-8')
        snapshot = self.root / 'updates/operation-progress.json'
        if not snapshot.exists():
            snapshot.write_text(json.dumps(dict(operationId='test', operation='install', phase='dependencies', status='running', startedAt=int(time.time()))), encoding='utf-8')
        command = [str(CONTROL), '--uv-progress', str(request)]
        if powershell:
            escape = lambda p: str(p).replace("'", "''")
            script = self.root / 'bridge.ps1'
            script.write_text(f"$env:PDF2ZH_WINDOWS_APP_ROOT='{escape(self.root)}'\n"
                             "$env:PDF2ZH_WINDOWS_REGISTRY_KEY='HKCU:\\Software\\pdf2zh-progress-bridge-test'\n"
                             f". '{escape(ROOT / 'scripts/windows/common.ps1')}'\n"
                             f". '{escape(ROOT / 'scripts/windows/runtime.ps1')}'\n"
                             f"$GuiSource='{escape(CONTROL)}'\n$script:RuntimeUv='{escape(UV)}'\n"
                             f"$request=Get-Content -Raw -Encoding utf8 -LiteralPath '{escape(request)}' | ConvertFrom-Json\n"
                             "Invoke-Uv -Arguments $request.arguments\n", encoding='utf-8-sig')
            command = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script)]
        signal = self.root / 'cancel.signal'
        cancelled_at = []
        if cancel:
            self.environment['PDF2ZH_INSTALL_CANCEL_FILE'] = str(signal)
            def request_cancel():
                if self.server.download_started.wait(15):
                    time.sleep(.4)
                    cancelled_at.append(time.monotonic())
                    signal.write_text('cancel')
            worker = threading.Thread(target=request_cancel)
            worker.start()
        result = subprocess.run(command, env=self.environment, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=90)
        if cancel:
            worker.join(1)
            self.assertTrue(cancelled_at, result.stdout + result.stderr)
            self.assertLess(time.monotonic() - cancelled_at[0], 5)
            signal.unlink(missing_ok=True)
            self.environment.pop('PDF2ZH_INSTALL_CANCEL_FILE', None)
        events = [json.loads(line[len('PDF2ZH_PROGRESS '):]) for line in result.stdout.splitlines() if line.startswith('PDF2ZH_PROGRESS ')]
        report = ROOT / '.tmp/uv-progress-validation'
        report.mkdir(exist_ok=True)
        (report / f'{self._testMethodName}-{label}.json').write_text(json.dumps(dict(exit=result.returncode, events=events, output=result.stdout, error=result.stderr, bytesSent=self.server.bytes_sent), ensure_ascii=False, indent=2), encoding='utf-8')
        return result, events

    def test_cancel_conpty_download_and_retry(self):
        result, events = self.invoke('cancelled', cancel=True)
        self.assertEqual(result.returncode, 1223, result.stdout + result.stderr)
        self.assertTrue(any(f['status'] == 'cancelled' for f in events[-1]['files']))
        result, _ = self.invoke('retry-after-cancel')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_cancel_pipe_fallback_without_waiting_for_output(self):
        result, _ = self.invoke('cancelled-pipes', version='future-version', cancel=True)
        self.assertEqual(result.returncode, 1223, result.stdout + result.stderr)

    def test_parallel_real_progress_and_cache_reuse(self):
        result, events = self.invoke('first')
        self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr)
        self.assertTrue(events, result.stdout)
        self.assertEqual(len(events[-1]['files']), 2, result.stdout[-3000:])
        self.assertTrue(any(len([f for f in e['files'] if f['status'] == 'downloading']) == 2 for e in events))
        for file in events[-1]['files']:
            samples = [f['completed'] for e in events for f in e['files'] if f['id'] == file['id']]
            self.assertGreater(len(set(samples)), 3)
            self.assertEqual(samples, sorted(samples))
            self.assertEqual(file['status'], 'downloaded')
        self.assertEqual(events[-1]['status'], 'running')
        first_bytes = self.server.bytes_sent
        result, _ = self.invoke('cached')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.server.bytes_sent, first_bytes, 'Cache reuse must not download wheel bodies again')
        subprocess.run([str(UV), 'cache', 'clean', 'progress-beta'], env=self.environment, capture_output=True, check=True, timeout=30)
        result, _ = self.invoke('partial-cache')
        self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr)
        self.assertEqual(self.server.bytes_sent - first_bytes, len(self.server.payloads['/progress_beta-1.0-py3-none-any.whl']))

    def test_completed_small_package_is_reported_while_large_one_downloads(self):
        self.server.payloads['/progress_beta-1.0-py3-none-any.whl'] = wheel('progress_beta', 256 * 1024)
        result, events = self.invoke('individual-completion')
        self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr)
        overlapping = [e for e in events if
                       any(f['name'] == 'progress-beta' and f['status'] == 'downloaded' for f in e['files']) and
                       any(f['name'] == 'progress-alpha' and f['status'] == 'downloading' for f in e['files'])]
        self.assertTrue(overlapping, 'Completed small wheel stayed downloading until the entire batch finished')
        self.assertTrue(all(e['status'] == 'running' for e in overlapping))
        seen_complete = False
        for event in events:
            for file in event['files']:
                if file['name'] == 'progress-beta':
                    if seen_complete:
                        self.assertEqual(file['status'], 'downloaded', 'A stale terminal frame reopened a completed download')
                    seen_complete |= file['status'] == 'downloaded'

    def test_interruption_then_retry_preserves_identity_and_attempt(self):
        self.server.interrupt = True
        result, events = self.invoke('broken')
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(any(f['status'] == 'failed' for f in events[-1]['files']), result.stdout)
        self.server.interrupt = False
        result, after = self.invoke('retry')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        before_attempts = {f['id']: f['attempt'] for f in events[-1]['files']}
        self.assertTrue(all(f['attempt'] > before_attempts[f['id']] for f in after[-1]['files']))

    def test_unsupported_output_contract_falls_back_without_restarting(self):
        result, events = self.invoke('fallback', 'future-version')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(events[-1]['progressUnavailable'])
        self.assertEqual(self.server.bytes_sent, sum(map(len, self.server.payloads.values())))
        self.assertFalse(any(f['total'] for f in events[-1]['files']))

    def test_powershell_bridge_preserves_file_progress(self):
        result, events = self.invoke('powershell', powershell=True)
        self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr)
        self.assertEqual(len(events[-1]['files']), 2)
        self.assertTrue(all(f['status'] == 'downloaded' for f in events[-1]['files']))
        self.assertGreater(len(events), 4)

    def test_unknown_length_has_no_invented_percentage(self):
        self.server.unknown_length = True
        result, events = self.invoke('unknown')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # uv can learn a second archive's size from its wheel metadata. At least
        # one remains unknown, and every observation of that file must stay null.
        unknown = {f['id'] for f in events[-1]['files'] if f['total'] is None}
        self.assertTrue(unknown, result.stdout[-3000:])
        self.assertFalse(any(f['total'] for e in events for f in e['files'] if f['id'] in unknown))

    def test_python_download_is_measured_before_integrity_failure(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr('python/progress-test.bin', os.urandom(4 * 1024 * 1024))
        self.server.payloads = {'/python.zip': stream.getvalue()}
        metadata = self.root / 'python-downloads.json'
        metadata.write_text(json.dumps({'test': dict(name='cpython', arch=dict(family='x86_64', variant=None), os='windows', libc='none',
                major=3, minor=13, patch=15, prerelease=None, variant=None, build=None,
                url=f'http://127.0.0.1:{self.server.server_port}/python.zip', sha256='0'*64)}), encoding='utf-8')
        # Wrong digest deliberately ends before installing/executing any Python.
        args = ['python', 'install', '3.13.15', '--no-bin', '--no-registry', '--no-config', '--python-downloads-json-url', str(metadata)]
        request = self.root / 'request.json'
        request.write_text(json.dumps(dict(executable=str(UV), arguments=args, root=str(self.root), uvVersion='0.12.10')), encoding='utf-8')
        (self.root / 'updates/operation-progress.json').write_text(json.dumps(dict(operationId='python-test', operation='install', phase='python', status='running', startedAt=int(time.time()))), encoding='utf-8')
        result = subprocess.run([str(CONTROL), '--uv-progress', str(request)], env=self.environment, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
        events = [json.loads(line[len('PDF2ZH_PROGRESS '):]) for line in result.stdout.splitlines() if line.startswith('PDF2ZH_PROGRESS ')]
        report = ROOT / '.tmp/uv-progress-validation/python.json'
        report.parent.mkdir(exist_ok=True)
        report.write_text(json.dumps(dict(exit=result.returncode, events=events, output=result.stdout, error=result.stderr), ensure_ascii=False, indent=2), encoding='utf-8')
        self.assertNotEqual(result.returncode, 0)
        samples = [f['completed'] for e in events for f in e['files'] if f['phase'] == 'python-download']
        self.assertGreater(len(set(samples)), 3, result.stdout + result.stderr)
        self.assertEqual(samples, sorted(samples))


if __name__ == '__main__':
    unittest.main()
