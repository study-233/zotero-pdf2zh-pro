"""Exercise real PowerShell progress/download output with an isolated local HTTP server."""
import hashlib
import http.server
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = b"local progress test\n" * 10000


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/stalled-headers":
            self.server.download_started.set()
            time.sleep(4)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(PAYLOAD)))
        self.end_headers()
        if self.path == "/stalled-body":
            self.wfile.write(PAYLOAD[:100])
            self.wfile.flush()
            self.server.download_started.set()
            time.sleep(4)
            return
        self.wfile.write(PAYLOAD if self.path == "/complete" else PAYLOAD[:100])
        self.close_connection = True

    def log_message(self, *_):
        pass


@unittest.skipUnless(os.name == "nt", "Windows PowerShell is required")
class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pdf2zh-progress-")
        self.root = Path(self.temporary.name)
        (self.root / "updates").mkdir()
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.temporary.cleanup()

    def run_script(self, body, common_source=None, explicit_root=True):
        script = self.root / "check.ps1"
        escape = lambda path: str(path).replace("'", "''")
        script.write_text(f". '{escape(common_source or ROOT / 'scripts/windows/common.ps1')}'\n"
                          f". '{escape(ROOT / 'scripts/windows/runtime.ps1')}'\n" + body, encoding="utf-8-sig")
        environment = os.environ.copy()
        environment["PDF2ZH_WINDOWS_APP_ROOT"] = str(self.root)
        environment["PDF2ZH_WINDOWS_REGISTRY_KEY"] = r"HKCU:\Software\pdf2zh-progress-test"
        environment["PSModulePath"] = r"C:\Program Files\PowerShell\7\Modules"
        if not explicit_root:
            environment.pop("PDF2ZH_WINDOWS_APP_ROOT", None)
        result = subprocess.run([str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
                                 "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                                env=environment, capture_output=True, text=True, encoding="utf-8", timeout=40)
        return result

    def test_native_hash_works_without_module_autoload_and_progress_survives_restart(self):
        (self.root / "input.bin").write_bytes(PAYLOAD)
        result = self.run_script("$PSModuleAutoLoadingPreference='None'\n"
                                 "$digest=Get-ProductFileSha256 (Join-Path $AppRoot 'input.bin')\n"
                                 "Write-Host $digest\n"
                                 "Write-OperationProgress 'dependencies' '准备依赖'\n"
                                 "Write-OperationProgress 'verifying' '校验文件'\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(hashlib.sha256(PAYLOAD).hexdigest(), result.stdout)
        events = [json.loads(line.removeprefix("PDF2ZH_PROGRESS ")) for line in result.stdout.splitlines() if line.startswith("PDF2ZH_PROGRESS ")]
        self.assertEqual(events[0]["operationId"], events[1]["operationId"])
        self.assertIsNone(events[0]["total"])
        saved = json.loads((self.root / "updates/operation-progress.json").read_text(encoding="utf-8"))
        result = self.run_script("Write-OperationProgress 'complete' '完成' -Status 'completed'")
        self.assertEqual(result.returncode, 0, result.stderr)
        after = json.loads((self.root / "updates/operation-progress.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["operationId"], after["operationId"])
        self.assertEqual(after["status"], "completed")

    def test_download_reports_real_bytes_and_rejects_a_short_response(self):
        url = f"http://127.0.0.1:{self.server.server_port}"
        result = self.run_script(f"Save-ProgressDownload '{url}/complete' (Join-Path $AppRoot 'download.part') '下载文件'")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "download.part").read_bytes(), PAYLOAD)
        saved = json.loads((self.root / "updates/operation-progress.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["completed"], len(PAYLOAD))
        self.assertEqual(saved["total"], len(PAYLOAD))
        result = self.run_script(f"Save-ProgressDownload '{url}/short' (Join-Path $AppRoot 'short.part') '下载文件'")
        self.assertNotEqual(result.returncode, 0)
        self.assertLess((self.root / "short.part").stat().st_size, len(PAYLOAD))

    def test_cancel_stalled_download_then_retry(self):
        for endpoint in ("stalled-headers", "stalled-body"):
            with self.subTest(endpoint=endpoint):
                signal = self.root / "cancel.signal"
                self.server.download_started = threading.Event()
                requested = []
                def cancel():
                    if self.server.download_started.wait(10):
                        requested.append(time.monotonic())
                        signal.write_text("cancel")
                worker = threading.Thread(target=cancel)
                worker.start()
                url = f"http://127.0.0.1:{self.server.server_port}/{endpoint}"
                result = self.run_script(
                    "$env:PDF2ZH_INSTALL_CANCEL_FILE=Join-Path $AppRoot 'cancel.signal'\n"
                    "try {\n"
                    f"Save-ProgressDownload '{url}' (Join-Path $AppRoot 'cancelled.part') '下载文件'\n"
                    "} catch [OperationCanceledException] {\n"
                    "Write-OperationProgress 'cancelled' '安装已取消' -Status 'cancelled'\nexit 1223\n}\n")
                worker.join(1)
                self.assertTrue(requested, result.stderr)
                self.assertLess(time.monotonic() - requested[0], 3)
                self.assertEqual(result.returncode, 1223, result.stderr)
                value = json.loads((self.root / 'updates/operation-progress.json').read_text(encoding='utf-8'))
                self.assertEqual(value['status'], 'cancelled')
                signal.unlink()
                # The cancelled request released its stream/file handles; retry works.
                result = self.run_script(
                    f"Save-ProgressDownload 'http://127.0.0.1:{self.server.server_port}/complete' "
                    "(Join-Path $AppRoot 'cancelled.part') '重试下载'")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((self.root / 'cancelled.part').read_bytes(), PAYLOAD)

    def test_candidate_recovery_scripts_retain_isolation_without_launch_environment(self):
        import shutil
        (self.root / "bin").mkdir()
        common = self.root / "bin/common.ps1"
        shutil.copyfile(ROOT / "scripts/windows/common.ps1", common)
        (self.root / "candidate-profile.json").write_text('{"candidate":true}', encoding="utf-8")
        result = self.run_script("Write-Host $AppRoot\nWrite-Host $ServerPort\nWrite-Host $InstallRegistryKey", common, False)
        self.assertEqual(result.returncode, 0, result.stderr)
        # PowerShell may expand the runner's 8.3 TEMP alias to its long path.
        self.assertTrue(self.root.samefile(result.stdout.splitlines()[0]), result.stdout)
        self.assertIn("19892", result.stdout)
        self.assertIn(r"HKCU:\Software\pdf2zh-native-candidate", result.stdout)

    def test_legacy_snapshot_and_file_rows_survive_powershell_phase_changes(self):
        snapshot = self.root / 'updates/operation-progress.json'
        snapshot.write_text(json.dumps(dict(operationId='old',operation='install',phase='python',status='running',startedAt=1)), encoding='utf-8')
        result = self.run_script("Write-OperationProgress 'dependencies' '准备依赖'")
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(snapshot.read_text(encoding='utf-8'))
        self.assertEqual(value['files'], [])
        value['files'] = [dict(id='a',name='numpy',status='downloaded',completed=100,total=100,sequence=3,attempt=1)]
        value['recentLogs'] = ['Downloaded numpy']
        snapshot.write_text(json.dumps(value), encoding='utf-8')
        result = self.run_script("Write-OperationProgress 'verifying' '校验文件'")
        self.assertEqual(result.returncode, 0, result.stderr)
        after = json.loads(snapshot.read_text(encoding='utf-8'))
        self.assertEqual(after['files'], value['files'])
        self.assertEqual(after['recentLogs'], value['recentLogs'])
        self.assertGreater(after['sequence'], value['sequence'])


if __name__ == "__main__":
    unittest.main()
