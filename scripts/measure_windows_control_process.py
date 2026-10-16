"""Measure an isolated controller process started directly in the tray.

This does not inspect or automate windows, and does not start a translation service.
Requires psutil. The reported condition is deliberately distinct from opening then closing a panel.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time

import psutil
from workspace import Workspace


def measure(binary: Path, kind: str, samples: int) -> dict:
    binary = binary.resolve(strict=True)
    results = []
    with Workspace().run('control-measure') as (work, report):
        for index in range(samples):
            directory = work / f'{kind}-{index}'
            directory.mkdir()
            environment = os.environ.copy()
            environment.update(PDF2ZH_WINDOWS_APP_ROOT=str(directory), PDF2ZH_WINDOWS_PORT='19896',
                               PDF2ZH_WINDOWS_REGISTRY_KEY=r'HKCU:\Software\pdf2zh-slint-gate',
                               PDF2ZH_WINDOWS_START_MENU_DIR=str(directory / 'shortcuts'),
                               PDF2ZH_WINDOWS_LIFECYCLE_TEST='1')
            arguments = ['--start-hidden'] if kind == 'slint' else ['--post-install', '--keep-stopped', '--candidate']
            started = time.perf_counter()
            owned = subprocess.Popen([str(binary), *arguments], env=environment, cwd=directory,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                process = psutil.Process(owned.pid)
                time.sleep(3)
                if owned.poll() is not None:
                    raise RuntimeError(f'{kind} exited before sampling: {owned.returncode}')
                before = sum(process.cpu_times()[:2])
                time.sleep(5)
                memory = process.memory_info()
                results.append({'workingSetBytes': memory.rss, 'privateBytes': getattr(memory, 'private', None),
                                'cpuMillisecondsOverFiveSeconds': (sum(process.cpu_times()[:2]) - before) * 1000,
                                'childCount': len(process.children()),
                                'sampledAfterMilliseconds': round((time.perf_counter() - started) * 1000)})
            finally:
                if owned.poll() is None:
                    owned.terminate()
                    owned.wait(timeout=10)
        result = {'kind': kind, 'binary': str(binary), 'sampling': 'Started directly in tray; panel never opened; service not started',
                  'samples': results, 'meanWorkingSetBytes': sum(r['workingSetBytes'] for r in results) / len(results)}
        (report / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--kind', choices=['native', 'slint'], required=True)
    parser.add_argument('--samples', type=int, default=3, choices=range(1, 4))
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    result = measure(arguments.binary, arguments.kind, arguments.samples)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
