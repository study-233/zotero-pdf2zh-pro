import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import windows_powershell as launcher


class EnvironmentTests(unittest.TestCase):
    def test_module_path_removed_only_from_child_and_case_insensitively(self):
        parent = {'PSModulePath': 'PS7', 'psmodulepath': 'other', 'PATH': 'tools', 'TEMP': 'temp'}
        self.assertEqual(launcher.child_environment(parent), {'PATH': 'tools', 'TEMP': 'temp'})
        self.assertEqual(parent['PSModulePath'], 'PS7')


@unittest.skipUnless(os.name == 'nt', 'Windows process regression')
class NativeTests(unittest.TestCase):
    def test_cleanup_failure_preserves_original_error_and_receipt(self):
        with tempfile.TemporaryDirectory(prefix='upgrade receipt ') as directory:
            script = Path(directory)/'cleanup.ps1'
            helper = Path(__file__).with_name('test_windows_process_helpers.ps1').resolve()
            script.write_text("param([string]$Report,[string]$Helper)\n. $Helper\n"
                              "function Stop-IsolatedTestProcesses {throw 'cleanup fixture failure'}\n"
                              "try {throw 'original fixture failure'} catch {$failure=$_}\n"
                              "$run=@{report=$Report;work=$Report}\n"
                              "Complete-IsolatedUpgrade -Run $run -Roots @($Report) -Registry 'unused' "
                              "-Result @{status='failed'} -Failure $failure\n", encoding='utf-8-sig')
            result = subprocess.run([sys.executable, launcher.__file__, str(script), '-Report', directory,
                                     '-Helper', str(helper)], capture_output=True, text=True, timeout=40)
            self.assertEqual(result.returncode, 0, result.stderr)
            import json
            receipt = json.loads((Path(directory)/'validation.json').read_text(encoding='utf-8-sig'))
            self.assertEqual(receipt['failure'], 'original fixture failure')
            self.assertEqual(receipt['cleanupFailure'], 'cleanup fixture failure')

    def test_intermediate_python_with_poisoned_modules_preserves_arguments_and_exit(self):
        with tempfile.TemporaryDirectory(prefix='powershell 中文 ') as directory:
            script = Path(directory)/'probe 中文.ps1'
            output = Path(directory)/'result.txt'
            script.write_text("param([string]$Output,[string]$Value)\n"
                              "$ErrorActionPreference='Stop'\n"
                              "$null=Get-FileHash -LiteralPath $PSCommandPath\n"
                              "[IO.File]::WriteAllText($Output,$Value)\nexit 17\n", encoding='utf-8-sig')
            environment = dict(os.environ, PSModulePath=str(Path(directory)/'invalid PS7 modules'))
            result = subprocess.run([sys.executable, str(Path(launcher.__file__)), str(script),
                                     '-Output', str(output), '-Value', '中文 spaces & $literal'],
                                    env=environment, capture_output=True, text=True, timeout=40)
            self.assertEqual(result.returncode, 17, result.stderr)
            self.assertEqual(output.read_text(encoding='utf-8'), '中文 spaces & $literal')

    def test_powershell7_git_bash_python_chain(self):
        pwsh = shutil.which('pwsh')
        bash = Path(shutil.which('git') or 'git').parent.parent/'bin/bash.exe'
        if not pwsh or not bash.is_file():
            self.skipTest('PowerShell 7 and Git Bash are required')
        import base64
        import shlex
        command = ' '.join(shlex.quote(x.replace('\\', '/')) for x in
                           [sys.executable, launcher.__file__, '--preflight-only'])
        # An intermediate Git Bash must pass the PS7 module environment to Python.
        code = "& '"+str(bash).replace("'", "''")+"' --noprofile --norc -c '"+command.replace("'", "''")+"'; exit $LASTEXITCODE"
        encoded = base64.b64encode(code.encode('utf-16-le')).decode()
        result = subprocess.run([pwsh, '-NoProfile', '-EncodedCommand', encoded],
                                capture_output=True, text=True, timeout=40)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Windows PowerShell preflight', result.stderr)


if __name__ == '__main__':
    unittest.main()
