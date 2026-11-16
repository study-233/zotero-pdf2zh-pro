"""Promote public documentation after the latest stable release has downloadable clients."""
import argparse
import base64
import json
import subprocess

from check_docs import MARKER, VERSION_PATTERN, public_version
from release_control import REPO, ROOT, api, command
from release_policy import version

API = f'repos/{REPO}'
CLIENTS = {'zotero-pdf2zh-pro.xpi', 'zotero-pdf2zh-pro-windows-x64-setup.exe'}
DOC_FILES = {'README.md', 'docs/user-guide.md', 'docs/glossary-downloads.md',
             'website/routes.json', 'website/screenshots.json'}
DOC_DIRS = ('assets/', 'website/src/content/docs/', 'website/src/app/',
            'website/src/features/', 'website/src/shared/')


def check_release(expected):
    version(expected)
    release = api(f'{API}/releases/latest')
    if release['draft'] or release['prerelease'] or release['tag_name'] != f'v{expected}':
        raise ValueError(f'Latest public stable release must be v{expected}')
    available = {a['name'] for a in release['assets'] if a['state'] == 'uploaded' and a['size'] > 0}
    if CLIENTS - available:
        raise ValueError(f'Missing public installers: {sorted(CLIENTS - available)}')


def readme(ref):
    file = api(f'{API}/contents/README.md?ref={ref}')
    return base64.b64decode(file['content']).decode('utf-8'), file['sha']


def promote_pr(number, expected):
    endpoint = f'{API}/pulls/{number}'
    pr = api(endpoint)
    if pr['base']['repo']['full_name'] != REPO or pr['base']['ref'] != 'main':
        raise ValueError('Documentation PR must target main in the release repository')
    if pr['merged']:
        return
    if pr['state'] != 'open' or pr['draft'] or pr['head']['repo']['full_name'] != REPO:
        raise ValueError('Expected an open, ready documentation PR in the release repository')
    pages = json.loads(command(['gh', 'api', '--paginate', '--slurp', endpoint + '/files?per_page=100']))
    files = [file for page in pages for file in page]
    if len(files) != pr['changed_files']:
        raise ValueError('Incomplete documentation PR file list')
    for file in files:
        for path in filter(None, (file['filename'], file.get('previous_filename'))):
            if path not in DOC_FILES and not path.startswith(DOC_DIRS):
                raise ValueError(f'Unexpected file in documentation PR: {path}')
    sha = pr['head']['sha']
    if public_version(readme(sha)[0]) != expected:
        raise ValueError('Documentation PR version differs from the release')
    check_release(expected)
    command(['gh', 'pr', 'merge', str(number), '--repo', REPO, '--merge', '--match-head-commit', sha])
    if not api(endpoint)['merged']:
        raise ValueError('Documentation PR is awaiting merge; resume after it merges')


def publish(expected, number=None):
    check_release(expected)
    if number:
        promote_pr(number, expected)
    text, sha = readme('main')
    documented = public_version(text)
    if documented == expected:
        return
    if number or version(documented) > version(expected):
        raise ValueError(f'Unexpected main README version: {documented}')
    updated = VERSION_PATTERN.sub(f'{MARKER} `{expected}`', text)
    payload = {'message': f'docs: publish v{expected}', 'branch': 'main', 'sha': sha,
               'content': base64.b64encode(updated.encode('utf-8')).decode('ascii')}
    check_release(expected)
    subprocess.run(['gh', 'api', '--method', 'PUT', f'{API}/contents/README.md', '--input', '-'],
                   input=json.dumps(payload).encode(), stdout=subprocess.DEVNULL, check=True, timeout=60)
    if public_version(readme('main')[0]) != expected:
        raise ValueError('Public README update could not be verified')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    commands.add_parser('verify').add_argument('version')
    promote = commands.add_parser('publish')
    promote.add_argument('version')
    promote.add_argument('--pr', type=int)
    args = parser.parse_args()
    if args.action == 'publish':
        publish(args.version, args.pr)
    else:
        if public_version((ROOT / 'README.md').read_text(encoding='utf-8')) != args.version:
            raise ValueError('Checked-out README differs from the requested deployment version')
        check_release(args.version)
    print(f'Public documentation verified for v{args.version}')


if __name__ == '__main__':
    main()
