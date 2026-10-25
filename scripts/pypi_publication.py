"""Probe or await the exact verified PyPI files using one bounded release gate."""
import argparse
import json
import math
from pathlib import Path
import re
import ssl
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from workspace import save


class PublicationConflict(ValueError):
    pass


class Pending(Exception):
    def __init__(self, message, retry_after=0):
        super().__init__(message)
        self.retry_after = retry_after


def expected_files(manifest):
    version = manifest['version']
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('Expected a stable release version')
    names = [f'zotero_pdf2zh_pro-{version}-py3-none-any.whl', f'zotero_pdf2zh_pro-{version}.tar.gz']
    result = {name: manifest['artifacts'][name] for name in names}
    for item in result.values():
        if not isinstance(item.get('size'), int) or item['size'] <= 0 or not re.fullmatch('[0-9a-f]{64}', item.get('sha256', '')):
            raise ValueError('Invalid expected PyPI file identity')
    return result


def fetch(url, timeout):
    request = Request(url, headers={'Accept': 'application/vnd.pypi.simple.v1+json' if '/simple/' in url else 'application/json',
                                    'User-Agent': 'zotero-pdf2zh-pro-release/1'})
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.load(response), dict(response.headers)
    except HTTPError as error:
        if error.code in (404, 408, 429) or 500 <= error.code <= 599:
            retry = error.headers.get('Retry-After', '0')
            raise Pending(f'{url}: HTTP {error.code}; cache={error.headers.get("X-Cache", "unknown")}',
                          int(retry) if retry.isdigit() else 0) from error
        raise ValueError(f'{url}: HTTP {error.code}') from error
    except URLError as error:
        if isinstance(error.reason, ssl.SSLCertVerificationError):
            raise ValueError(f'TLS verification failed: {url}') from error
        raise Pending(f'{url}: {error.reason}') from error
    except (TimeoutError, ConnectionError) as error:
        raise Pending(f'{url}: {error}') from error


def inspect_files(rows, expected, simple=False):
    if not isinstance(rows, list):
        raise ValueError('Invalid PyPI file listing')
    missing = []
    for name, identity in expected.items():
        matches = [row for row in rows if row.get('filename') == name]
        if not matches:
            missing.append(name)
            continue
        if len(matches) != 1:
            raise PublicationConflict(f'Duplicate PyPI filename: {name}')
        item = matches[0]
        digest = item.get('hashes' if simple else 'digests', {}).get('sha256')
        if (digest != identity['sha256'] or (not simple and item.get('size') != identity['size'])
                or item.get('yanked', False)):
            raise PublicationConflict(f'PyPI content mismatch or yanked file: {name}')
    return missing


def bounded_fetch(url, timeout):
    # A socket timeout does not bound DNS or a slow streaming response. An owned
    # worker gives each HTTP observation a real wall-clock limit on both hosts.
    try:
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--request', url, str(timeout)],
                                capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        raise Pending(f'{url}: request exceeded {timeout:.1f}s') from error
    if result.returncode:
        raise ValueError(f'PyPI request worker failed: {result.stderr.strip()}')
    response = json.loads(result.stdout)
    if response['status'] == 'pending':
        raise Pending(response['error'], response.get('retryAfter', 0))
    if response['status'] != 'passed':
        raise ValueError(response['error'])
    return response['data'], response['headers']


def request_worker(url, timeout):
    try:
        data, headers = fetch(url, timeout)
        response = {'status': 'passed', 'data': data, 'headers': headers}
    except Pending as error:
        response = {'status': 'pending', 'error': str(error), 'retryAfter': error.retry_after}
    except (ValueError, OSError) as error:
        response = {'status': 'failed', 'error': str(error)}
    print(json.dumps(response))


def wait_for_publication(manifest, timeout=600, fetcher=bounded_fetch, clock=time.monotonic, sleep=time.sleep, log=print):
    expected = expected_files(manifest)
    if not math.isfinite(timeout) or timeout < 0:
        raise ValueError('Timeout must be nonnegative')
    started = clock()
    deadline = started + timeout
    delay = 5
    attempt = 0
    last = 'not checked'
    while True:
        attempt += 1
        observations = {}
        retry_after = 0
        complete = True
        endpoints = [('json', f'https://pypi.org/pypi/zotero-pdf2zh-pro/{manifest["version"]}/json'),
                     ('simple', 'https://pypi.org/simple/zotero-pdf2zh-pro/')]
        for kind, url in endpoints:
            remaining = deadline - clock()
            if timeout and remaining <= 0:
                complete = False
                last = 'total deadline reached'
                break
            try:
                data, headers = fetcher(url, min(10, remaining) if timeout else 10)
                missing = inspect_files(data.get('files' if kind == 'simple' else 'urls'), expected, kind == 'simple')
                observations[kind] = {'missing': missing, 'cache': headers.get('X-Cache'), 'etag': headers.get('ETag')}
                if missing:
                    complete = False
                    last = f'{kind}: awaiting {", ".join(missing)}'
            except Pending as error:
                complete = False
                last = str(error)
                retry_after = max(retry_after, error.retry_after)
                observations[kind] = {'error': last}
        elapsed = clock() - started
        if complete and (not timeout or clock() <= deadline):
            return {'schemaVersion': 1, 'status': 'passed', 'version': manifest['version'],
                    'commit': manifest.get('commit'), 'artifacts': expected, 'attempts': attempt,
                    'elapsedSeconds': round(elapsed, 2), 'observations': observations}
        log(f'PyPI {manifest["version"]}: {last}; waited {elapsed:.1f}/{timeout}s; {observations}', flush=True)
        if not timeout or clock() >= deadline:
            raise Pending(f'Public PyPI files not yet verified: {last}; resume the visibility stage using the same artifacts')
        sleep(min(max(delay, retry_after), deadline - clock()))
        delay = min(delay * 2, 30)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--timeout', type=float, default=600)
    parser.add_argument('--receipt', type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding='utf-8-sig'))
    if not 0 <= args.timeout <= 1800:
        raise ValueError('Publication wait must be between 0 and 1800 seconds')
    try:
        result = wait_for_publication(manifest, args.timeout)
    except Pending as error:
        if args.receipt:
            save(args.receipt, {'schemaVersion': 1, 'status': 'pending', 'version': manifest['version'],
                                'commit': manifest.get('commit'), 'artifacts': expected_files(manifest), 'error': str(error)})
        raise
    if args.receipt:
        save(args.receipt, result)
    print(f'Public PyPI wheel and sdist match; JSON and Simple indexes ready ({result["elapsedSeconds"]}s)')


if __name__ == '__main__':
    try:
        if len(sys.argv) == 4 and sys.argv[1] == '--request':
            request_worker(sys.argv[2], float(sys.argv[3]))
        else:
            main()
    except Pending as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
    except (ValueError, KeyError, OSError) as error:
        print(f'PyPI verification stopped: {error}', file=sys.stderr)
        sys.exit(2)
