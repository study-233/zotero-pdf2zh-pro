"""Record measured Slint migration gates without treating unperformed UI checks as passes."""
import argparse
import hashlib
import json
from pathlib import Path

from build_windows_installer import verify_installer
from windows_pe import validate_release_pe, imports

MAX_INSTALLER_BYTES = 40_000_000
MAX_EXTRA_WORKING_SET = 30 * 1024 * 1024
UI_CHECKS = ('chineseDisplay', 'imeCandidatePosition', 'clipboard', 'fontFallback',
             'layout100', 'layout150', 'layout200', 'trayRestore', 'noHiddenRepaint')


def assess(binary, installer, version, baseline=None, candidate=None, ui=None, closed_baseline=None, closed_candidate=None):
    data = binary.read_bytes()
    checks = {}
    try:
        validate_release_pe(data)
        checks['systemDependencies'] = {'status': 'passed', 'imports': sorted(imports(data))}
    except (ValueError, AssertionError) as error:
        checks['systemDependencies'] = {'status': 'failed', 'reason': str(error)}
    report = verify_installer(installer, version)
    checks['installerBudget'] = {'status': 'passed' if report['size'] <= MAX_INSTALLER_BYTES else 'failed',
                                 'measuredBytes': report['size'], 'maximumBytes': MAX_INSTALLER_BYTES}
    if baseline and candidate:
        same_condition = baseline['sampling'] == candidate['sampling']
        difference = candidate['meanWorkingSetBytes'] - baseline['meanWorkingSetBytes']
        checks['trayStartupMemory'] = {'status': 'passed' if same_condition and difference <= MAX_EXTRA_WORKING_SET else 'failed',
                                      'differenceBytes': difference, 'maximumExtraBytes': MAX_EXTRA_WORKING_SET,
                                      'condition': candidate['sampling']}
        # Starting hidden alone does not prove memory after rendering and closing a panel.
    else:
        checks['trayStartupMemory'] = {'status': 'pending'}
    checks['memoryAfterPanelClose'] = {'status': 'pending'}
    if closed_baseline and closed_candidate:
        same_condition = closed_baseline['sampling'] == closed_candidate['sampling']
        closed_condition = closed_candidate['sampling'].startswith('Panel opened then closed to tray;')
        difference = closed_candidate['meanWorkingSetBytes'] - closed_baseline['meanWorkingSetBytes']
        checks['memoryAfterPanelClose'] = {'status': 'passed' if same_condition and closed_condition and difference <= MAX_EXTRA_WORKING_SET else 'failed',
                                          'differenceBytes': difference, 'maximumExtraBytes': MAX_EXTRA_WORKING_SET,
                                          'condition': closed_candidate['sampling']}
    for name in UI_CHECKS:
        result = (ui or {}).get(name)
        checks[name] = result if isinstance(result, dict) and result.get('status') in ('passed','failed') and result.get('evidence') else {'status': 'pending'}
    status = 'failed' if any(c['status'] == 'failed' for c in checks.values()) else 'pending' if any(c['status'] == 'pending' for c in checks.values()) else 'passed'
    return {'status': status, 'migrationAllowed': status == 'passed', 'probeOnly': True,
            'slintVersion': '1.18.1', 'renderer': 'winit-skia-software',
            'binary': {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()},
            'installer': report, 'checks': checks}


def read_optional(path):
    return json.loads(path.read_text(encoding='utf-8')) if path else None


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--installer', type=Path, required=True)
    parser.add_argument('--version', required=True, help='Expected candidate version, verified against the installer')
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--candidate', type=Path)
    parser.add_argument('--ui-results', type=Path)
    parser.add_argument('--closed-baseline', type=Path)
    parser.add_argument('--closed-candidate', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = assess(args.binary, args.installer, args.version, read_optional(args.baseline), read_optional(args.candidate), read_optional(args.ui_results), read_optional(args.closed_baseline), read_optional(args.closed_candidate))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
