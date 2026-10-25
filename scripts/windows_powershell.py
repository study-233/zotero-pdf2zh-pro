"""Launch Windows PowerShell without inheriting PowerShell 7's module paths."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def child_environment(environment=None):
    return {key: value for key, value in (os.environ if environment is None else environment).items()
            if key.upper() != 'PSMODULEPATH'}


def executable():
    if os.name != 'nt':
        raise RuntimeError('Windows PowerShell requires Windows')
    system = Path(os.environ['SystemRoot'])
    # A 32-bit caller needs Sysnative to reach the 64-bit system executable.
    directory = 'Sysnative' if os.environ.get('PROCESSOR_ARCHITEW6432') else 'System32'
    path = system / directory / 'WindowsPowerShell/v1.0/powershell.exe'
    if not path.is_file():
        raise RuntimeError(f'Windows PowerShell is unavailable: {path}')
    return str(path)


def preflight(program, environment):
    probe = """$ErrorActionPreference='Stop'
if ($PSVersionTable.PSEdition -ne 'Desktop' -or $PSVersionTable.PSVersion.Major -ne 5) {
    throw 'Windows PowerShell 5.1 is required.'
}
$commands=@('Get-FileHash','Expand-Archive','ConvertFrom-Json','ConvertTo-Json','Get-CimInstance')
$sources=@{}
foreach ($name in $commands) {$sources[$name]=(Get-Command $name -ErrorAction Stop).Source}
$null=Get-CimInstance Win32_Process -Filter "ProcessId=$PID" -ErrorAction Stop
@{version=$PSVersionTable.PSVersion.ToString();edition=$PSVersionTable.PSEdition;commands=$sources}|ConvertTo-Json -Compress
"""
    result = subprocess.run([program, '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', probe],
                            env=environment, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError(f'Windows PowerShell preflight failed ({program}): {result.stderr.strip()}')
    details = json.loads(result.stdout.lstrip('\ufeff'))
    print(f'Windows PowerShell preflight: {program}; {details}', file=sys.stderr, flush=True)
    return details


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preflight-only', action='store_true')
    parser.add_argument('script', nargs='?')
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    program, environment = executable(), child_environment()
    preflight(program, environment)
    if args.preflight_only:
        return 0
    if not args.script:
        parser.error('a script is required')
    return subprocess.call([program, '-NoLogo', '-NoProfile', '-NonInteractive',
                            '-ExecutionPolicy', 'Bypass', '-File', args.script, *args.arguments], env=environment)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
        print(f'Windows PowerShell: {error}', file=sys.stderr)
        sys.exit(1)
