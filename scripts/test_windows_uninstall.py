"""Verify default retention and explicit purge against an isolated installation."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'nt', 'Windows PowerShell required')
class UninstallTests(unittest.TestCase):
    def uninstall(self, purge):
        with tempfile.TemporaryDirectory(prefix='pdf2zh-uninstall-', dir=ROOT / '.tmp') as temporary:
            parent = Path(temporary)
            root = parent / 'installation'
            binary = root / 'bin'
            binary.mkdir(parents=True)
            for name in ('common.ps1', 'runtime.ps1', 'uninstall.ps1'):
                shutil.copy2(ROOT / 'scripts/windows' / name, binary / name)
            # This fixture owns no running service/control process. Keep the real
            # uninstall and cleanup logic while supplying a successful service stop.
            (binary / 'stop-server.ps1').write_text('$global:LASTEXITCODE=0\n', encoding='utf-8-sig')
            (root / 'runtime/versions').mkdir(parents=True)
            (root / 'runtime/uv').mkdir()
            (root / 'runtime/uv/uv.exe').write_bytes(b'fixture: never executed')
            protected = ['settings.json', 'data/translation.pdf', 'logs/control-panel.log']
            for name in protected + ['cache/download', 'updates/pending']:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('preserve or purge only by explicit choice', encoding='utf-8')
            outside = parent / 'unrelated.txt'
            outside.write_text('untouched')
            environment = os.environ.copy()
            environment['PDF2ZH_WINDOWS_APP_ROOT'] = str(root)
            environment['PDF2ZH_WINDOWS_REGISTRY_KEY'] = rf'HKCU:\Software\pdf2zh-uninstall-test-{uuid.uuid4().hex}'
            environment['PDF2ZH_WINDOWS_START_MENU_DIR'] = str(parent / 'menu')
            environment['PDF2ZH_WINDOWS_PORT'] = '19989'
            environment['TEMP'] = environment['TMP'] = str(parent)
            args = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                    str(binary / 'uninstall.ps1'), '-NonInteractive']
            if purge:
                args.append('-PurgeData')
            # Ownership queries and first-use PowerShell compilation can exceed
            # 20 seconds while the native installation suite is also running.
            result = subprocess.run(args, env=environment, capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
            deadline = time.monotonic() + 60
            while (list(parent.glob('zotero-pdf2zh-pro-cleanup-*.ps1')) or
                   (root.exists() if purge else binary.exists())) and time.monotonic() < deadline:
                time.sleep(.1)
            self.assertFalse(binary.exists(), 'Program files were not removed')
            for name in protected:
                self.assertEqual((root / name).exists(), not purge, name)
            self.assertFalse((root / 'runtime').exists())
            self.assertFalse((root / 'cache').exists())
            self.assertEqual(outside.read_text(), 'untouched')
            if purge:
                self.assertFalse(root.exists())

    def test_default_keeps_configuration_data_and_logs(self):
        self.uninstall(False)

    def test_explicit_purge_removes_configuration_data_and_logs(self):
        self.uninstall(True)


if __name__ == '__main__':
    unittest.main()
