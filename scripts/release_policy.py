"""Shared release test selection and reversible test-harness overlay."""
import argparse
import fnmatch
import hashlib
import json
from pathlib import Path
import re
import subprocess

POLICY_PATH = Path(__file__).with_name('release-policy.json')


def policy():
    data = json.loads(POLICY_PATH.read_text(encoding='utf-8'))
    if data['schemaVersion'] != 1:
        raise ValueError('Unsupported release policy')
    return data


def version(value):
    if not re.fullmatch(r'v?\d+\.\d+\.\d+', value):
        raise ValueError(f'Expected stable version: {value}')
    return tuple(map(int, value.removeprefix('v').split('.')))


def policy_digest():
    # Git checkout line endings differ between local Windows and Linux publishing.
    return hashlib.sha256(json.dumps(policy(),sort_keys=True,separators=(',', ':')).encode()).hexdigest()


def harness_digest(root):
    files = {name:hashlib.sha256((Path(root)/name).read_text(encoding='utf-8-sig').encode()).hexdigest()
             for name in policy()['harnessPaths']}
    return hashlib.sha256(json.dumps(files,sort_keys=True,separators=(',', ':')).encode()).hexdigest()


def previous_release(releases, candidate):
    eligible = [r for r in releases if not r['draft'] and not r['prerelease']
                and re.fullmatch(r'v\d+\.\d+\.\d+', r['tag_name'])
                and version(r['tag_name']) < version(candidate)]
    if not eligible:
        raise ValueError('No previous stable release; an explicit initial-release policy is required')
    result = max(eligible, key=lambda r: version(r['tag_name']))
    required = {'zotero-pdf2zh-pro-windows-x64.zip', 'windows-update.json'}
    if not required.issubset({a['name'] for a in result['assets']}):
        raise ValueError('Latest stable release is incomplete; do not silently choose an older baseline')
    return result


def select(changes, previous, candidate, full=False, compatibility=False):
    rules = policy()
    groups = {name: group for name, group in rules['riskGroups'].items()
              if any(fnmatch.fnmatchcase(p, pat) for p in changes for pat in group['paths'])}
    risky = [p for p in changes if any(fnmatch.fnmatchcase(p, pat)
                                     for group in groups.values() for pat in group['paths'])]
    unknown = [p for p in changes if not any(fnmatch.fnmatchcase(p, pat) for pat in rules['knownPaths'])
               and p not in risky]
    exhaustive = bool(full or compatibility)
    selected = rules['riskGroups'] if exhaustive or unknown else groups
    lifecycle = 'extended' if any(g['lifecycle'] == 'extended' for g in selected.values()) else 'standard'
    upgrades = {case for group in selected.values() for case in group['upgrades']}
    cases = [{'id': 'recent-upgrade', 'baseline': previous}]
    for case, baseline in [('oldest-upgrade', rules['currentLayoutBaseline']),
                           ('legacy-upgrade', rules['legacyBaseline'])]:
        if case in upgrades and version(baseline) < version(candidate) and baseline != previous:
            cases.append({'id': case, 'baseline': baseline})
    return {'schemaVersion': 2, 'version': candidate, 'previousVersion': previous,
            'policySha256': policy_digest(),
            'fullValidation': exhaustive, 'extendedValidation': lifecycle == 'extended' or len(cases) > 1,
            'lifecycle': lifecycle, 'riskGroups': list(groups), 'riskPaths': risky, 'unknownPaths': unknown,
            'reason': 'explicit compatibility/full request' if full or compatibility else
                      'unclassified changes' if unknown else ', '.join(groups) if groups else 'ordinary release',
            'cases': cases}


def changed_paths(root, old, new):
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=root, text=True, encoding='utf-8')
    names = git('diff', '--name-only', old, new).splitlines()
    # Ignore only this project's release markers, never dependency versions.
    markers = {
        'server/server.py': r'(?m)^(VERSION = ")[^"]+("\s*)$',
        'scripts/windows/common.ps1': r'(?m)(\$PackageVersion = ")[^"]+(" # release-version)',
        'server/pyproject.toml': r'(?m)^(version = ")[^"]+("\s*)$',
        'server/uv.lock': r'(\[\[package\]\]\nname = "zotero-pdf2zh-pro"\nversion = ")[^"]+(")',
        'windows-app/Cargo.toml': r'(?m)^(version = ")[^"]+("\s*)$',
        'windows-app/Cargo.lock': r'(\[\[package\]\]\nname = "zotero-pdf2zh-pro-control"\nversion = ")[^"]+(")',
    }
    result = []
    for name in names:
        if name in markers:
            try:
                texts = [git('show', f'{ref}:{name}') for ref in (old, new)]
                if re.sub(markers[name], r'\1<release>\2', texts[0]) == re.sub(markers[name], r'\1<release>\2', texts[1]):
                    continue
            except subprocess.CalledProcessError:
                pass  # A new/deleted file is always a meaningful change.
        result.append(name)
    return result


def harness(operation, source, root):
    paths = policy()['harnessPaths']
    for name in paths:
        path = Path(name)
        if path.is_absolute() or '..' in path.parts or path.parts[0] != 'scripts':
            raise ValueError(f'Unsafe harness path: {name}')
    if operation == 'overlay':
        subprocess.run(['git', 'restore', '--source', source, '--worktree', '--', *paths], cwd=root, check=True)
    else:
        for name in paths:
            exists = subprocess.run(['git', 'cat-file', '-e', f'HEAD:{name}'], cwd=root,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
            if exists:
                subprocess.run(['git', 'restore', '--source', 'HEAD', '--worktree', '--', name], cwd=root, check=True)
            else:
                target = root / name
                if target.parent.resolve() != (root / 'scripts').resolve():
                    raise ValueError(f'Unsafe harness removal: {name}')
                target.unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('overlay', 'restore'))
    parser.add_argument('source', nargs='?', default='HEAD')
    args = parser.parse_args()
    harness(args.operation, args.source, Path.cwd())
