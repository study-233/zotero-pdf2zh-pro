"""Manage local development outputs; never clean source, caches or user data."""
import argparse
from contextlib import contextmanager
import ctypes
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import threading
import time
import uuid
import zipfile

if sys.version_info < (3, 12):
    raise SystemExit('workspace.py requires Python 3.12 or newer.')


ROOT = Path(__file__).resolve().parents[1]
MARKER = '.workspace.json'
REPORT_DAYS = 30
BUILD_VERSIONS = 2
VERSION = re.compile(r'^(\d+)\.(\d+)\.(\d+)(?:[-.]([0-9A-Za-z.-]+))?$')
DEPENDENCIES = {'runtime', 'runtime312', 'runtime313', 'cache', 'python', 'uv-tools',
                'uv-bin', 'node_modules', '__pycache__', 'site-packages'}
EVIDENCE = {'.json', '.jsonl', '.log', '.txt', '.md', '.png', '.jpg', '.html',
            '.pdf', '.csv', '.ps1', '.py', '.cmd', '.sh', '.sha256'}


def linked(path):
    return path.is_symlink() or path.is_junction()


def checked(path, parent):
    path, parent = Path(os.path.abspath(path)), Path(os.path.abspath(parent))
    if path == parent or not path.is_relative_to(parent):
        raise ValueError(f'Path outside managed directory: {path}')
    if path.resolve() != path:
        raise ValueError(f'Path resolves through a link: {path}')
    for item in (path, *path.parents):
        if linked(item):
            raise ValueError(f'Linked path is not a managed directory: {item}')
        if item == ROOT or item == parent.parent:
            break
    return path


def alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == 'nt':
        from ctypes import wintypes
        api = ctypes.WinDLL('kernel32', use_last_error=True)
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = api.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5
        try:
            code = wintypes.DWORD()
            return not api.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            api.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except ProcessLookupError:
        return False


def save(path, data):
    temporary = path.with_suffix('.writing')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for attempt in range(5):
        try:
            temporary.replace(path)
            break
        except PermissionError as error:
            if getattr(error, 'winerror', None) not in (5, 32) or attempt == 4:
                raise
            time.sleep(0.05 * (attempt + 1))


def metadata(path, category):
    marker = path / MARKER
    if linked(marker):
        raise ValueError(f'Linked workspace marker: {marker}')
    data = json.loads(marker.read_text(encoding='utf-8'))
    if (not isinstance(data, dict) or data.get('schema') != 1 or data.get('category') != category or
            data.get('name') != path.name or not isinstance(data.get('created'), (int, float))
            or not math.isfinite(data['created']) or data['created'] <= 0
            or (data.get('owner_pid') is not None and not isinstance(data['owner_pid'], int))):
        raise ValueError(f'Invalid workspace marker: {marker}')
    if category == 'build':
        version_key(path.name)
    if 'finished' in data and (not isinstance(data['finished'], (int, float)) or not math.isfinite(data['finished'])):
        raise ValueError(f'Invalid finish timestamp: {marker}')
    return data


def kept(path, parent):
    for directory in (path, *path.parents):
        marker = directory / '.keep'
        if marker.exists() or marker.is_symlink():
            return True
        if directory == parent:
            break
    return False


def files(path, exclude=()):
    """Enumerate without traversing symbolic links or Windows junctions."""
    pending = [path]
    while pending:
        current = pending.pop()
        for entry in current.iterdir():
            if linked(entry):
                continue
            if entry.is_dir():
                if entry.name not in exclude:
                    pending.append(entry)
            elif entry.is_file():
                yield entry


def remove(path, parent):
    path = checked(path, parent)
    # Python >=3.12 rmtree does not traverse child symlinks or Windows junctions.

    def writable(function, name, error):
        if not isinstance(error[1], PermissionError):
            raise error[1]
        os.chmod(name, stat.S_IWRITE | stat.S_IREAD)
        function(name)

    shutil.rmtree(path, onerror=writable)


@contextmanager
def measure(operation, **details):
    """Record operation costs without letting telemetry replace the original error."""
    started = time.time()
    clock = time.monotonic()
    result = dict(operation=operation, started=started, pid=os.getpid(), status='failed', **details)
    try:
        yield result
        result['status'] = 'passed'
    finally:
        result['seconds'] = time.monotonic() - clock
        directory = os.environ.get('PDF2ZH_RELEASE_METRICS_DIR')
        if directory:
            try:
                target = Path(directory)
                target.mkdir(parents=True, exist_ok=True)
                with (target / f'python-{os.getpid()}.jsonl').open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(result, ensure_ascii=False) + '\n')
            except OSError as error:
                print(f'Cannot record release timing: {error}', file=sys.stderr, flush=True)
        print(f'{operation}: {result["status"]}; elapsed {result["seconds"]:.2f}s', file=sys.stderr, flush=True)


@contextmanager
def progress(phase, path):
    stopped = threading.Event()
    started = time.monotonic()
    def report():
        while not stopped.wait(15):
            print(f'{phase}: {path}; elapsed {time.monotonic()-started:.0f}s', file=sys.stderr, flush=True)
    worker = threading.Thread(target=report, daemon=True)
    print(f'{phase}: {path}', file=sys.stderr, flush=True)
    worker.start()
    try:
        with measure(phase, path=str(path)):
            yield
    finally:
        stopped.set()
        worker.join()


def version_key(value):
    match = VERSION.fullmatch(value)
    if not match:
        raise ValueError(f'Invalid build version: {value}')
    major, minor, patch, suffix = match.groups()
    prerelease = tuple((0, int(part)) if part.isdigit() else (1, part)
                       for part in (suffix or '').split('.'))
    return int(major), int(minor), int(patch), suffix is None, prerelease


class Workspace:
    def __init__(self, root=ROOT):
        self.root = Path(root).resolve()
        self.runs = self.root / '.tmp/runs'
        self.reports = self.root / '.local-dev/reports'
        self.builds = self.root / 'dist/releases'

    def entries(self, parent, category):
        if linked(parent):
            raise ValueError(f'Linked output root: {parent}')
        if not parent.exists():
            return
        for path in parent.iterdir():
            if not path.is_dir() or linked(path):
                continue
            try:
                checked(path, parent)
                yield path, metadata(path, category)
            except (OSError, ValueError):
                # Unknown files are reported by check(), never adopted for deletion.
                continue

    def plan(self, now=None):
        now = time.time() if now is None else now
        result = []
        for parent, category, days in ((self.runs, 'run', 1), (self.reports, 'report', REPORT_DAYS)):
            for path, data in self.entries(parent, category):
                timestamp = data.get('finished', data['created']) if category == 'report' else data['created']
                if (not kept(path, parent) and now - timestamp > days * 86400
                        and not alive(data.get('owner_pid'))):
                    result.append((path, parent, f'{category} older than {days} days'))
        builds = sorted(self.entries(self.builds, 'build'), key=lambda item: version_key(item[0].name), reverse=True)
        for path, data in builds[BUILD_VERSIONS:]:
            if not kept(path, self.builds) and not alive(data.get('owner_pid')):
                result.append((path, self.builds, f'outside latest {BUILD_VERSIONS} versions'))
        versions = {path.name for path, _ in builds}
        aliases = []
        for parent in (self.root / 'dist', self.root / 'server/dist', self.root / 'dist/release'):
            if parent.exists() and not linked(parent):
                for path in parent.iterdir():
                    match = re.fullmatch(r'(?:zotero-pdf2zh-pro-(.+)-source\.zip|zotero_pdf2zh_pro-(.+?)(?:-py3-none-any\.whl|\.tar\.gz))', path.name)
                    if match and path.is_file() and not linked(path):
                        version = next(value for value in match.groups() if value)
                        if VERSION.fullmatch(version):
                            versions.add(version)
                            aliases.append((path, parent, version))
        newest = set(sorted(versions, key=version_key, reverse=True)[:BUILD_VERSIONS])
        newest.update(path.name for path, _ in builds if kept(path, self.builds))
        for path, parent, version in aliases:
            if version not in newest and not kept(path, parent):
                checked(path, parent)
                result.append((path, parent, f'outside latest {BUILD_VERSIONS} versions'))
        return result

    def clean(self, apply=False, now=None):
        result = []
        for path, parent, reason in self.plan(now):
            try:
                size = sum(file.stat().st_size for file in files(path)) if path.is_dir() else path.stat().st_size
                row = {'path': str(path.relative_to(self.root)), 'bytes': size, 'reason': reason, 'deleted': False}
                if apply:
                    if kept(path, parent):
                        continue
                    if path.is_file():
                        checked(path, parent).unlink()
                    else:
                        # Recheck ownership immediately before deleting.
                        category = 'run' if parent == self.runs else 'report' if parent == self.reports else 'build'
                        data = metadata(path, category)
                        if alive(data.get('owner_pid')):
                            continue
                        if category == 'report' and (time.time() if now is None else now) - data.get('finished', data['created']) <= REPORT_DAYS * 86400:
                            continue
                        if category == 'run' and data.get('report'):
                            self.finish(path, 'failed')
                        else:
                            remove(path, parent)
                    row['deleted'] = True
                result.append(row)
            except FileNotFoundError:
                # Another finishing run may have reclaimed the same expired output.
                continue
        return result

    def create(self, kind, category='report', owner_pid=None):
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', kind):
            raise ValueError('Use a short lowercase name for the operation.')
        self.clean(apply=True)
        name = f'{time.strftime("%Y%m%dT%H%M%S", time.gmtime())}-{kind}-{uuid.uuid4().hex[:8]}'
        parent = self.runs if category == 'run' else self.reports
        path = checked(parent / name, parent)
        path.mkdir(parents=True)
        save(path / MARKER, {'schema': 1, 'category': category, 'name': name, 'kind': kind,
                            'created': time.time(), 'owner_pid': owner_pid or os.getpid(), 'status': 'running'})
        return path

    def start(self, kind, owner_pid=None):
        report = self.create(kind, owner_pid=owner_pid)
        work = self.create(kind, 'run', owner_pid=owner_pid)
        data = metadata(work, 'run')
        data['report'] = str(report.relative_to(self.root))
        save(work / MARKER, data)
        return {'work': str(work), 'report': str(report)}

    def finish(self, work, status='passed'):
        work = checked(Path(work), self.runs)
        data = metadata(work, 'run')
        if kept(work, self.runs):
            data.update(status=status, owner_pid=None)
            save(work / MARKER, data)
            return str(work)
        report = checked(self.root / data['report'], self.reports)
        metadata(report, 'report')
        with progress('Archiving validation evidence', work), zipfile.ZipFile(report / 'evidence.zip', 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for file in files(work, DEPENDENCIES):
                relative = file.relative_to(work)
                if (file.name != MARKER and not set(relative.parts[:-1]).intersection(DEPENDENCIES)
                        and file.suffix.lower() in EVIDENCE):
                    archive.write(file, relative.as_posix())
        with zipfile.ZipFile(report / 'evidence.zip') as archive:
            if archive.testzip() is not None:
                raise ValueError('Evidence archive validation failed; retaining work directory.')
        summary = metadata(report, 'report')
        summary.update(status=status, owner_pid=None, finished=time.time())
        save(report / MARKER, summary)
        summary['cleanupStatus'] = 'running'
        save(report / MARKER, summary)
        try:
            with progress('Removing isolated test environment', work):
                remove(work, self.runs)
            summary['cleanupStatus'] = 'complete'
        except OSError as error:
            summary.update(cleanupStatus='pending', cleanupPath=str(work), cleanupError=str(error))
            print(f'Temporary environment retained for cleanup: {work}: {error}', file=sys.stderr, flush=True)
        save(report / MARKER, summary)
        return str(report)

    @contextmanager
    def run(self, kind):
        run = self.start(kind)
        status = 'failed'
        try:
            yield Path(run['work']), Path(run['report'])
            status = 'passed'
        finally:
            try:
                self.finish(run['work'], status)
            except (OSError, ValueError) as error:
                if status == 'passed':
                    raise
                print(f'workspace: retaining failed run {run["work"]}: {error}', file=sys.stderr)

    def snapshot(self, version, inputs):
        version_key(version)
        destination = checked(self.builds / version, self.builds)
        if destination.exists():
            metadata(destination, 'build')
            if kept(destination, self.builds):
                raise ValueError(f'Build is marked for long-term retention: {destination}')
        sources = [Path(file).absolute() for file in inputs]
        if not sources or len({file.name for file in sources}) != len(sources):
            raise ValueError('Build inputs must have distinct filenames.')
        for source in sources:
            checked(source, self.root)
            if not source.is_file() or source.name == MARKER:
                raise ValueError(f'Invalid build input: {source}')
        with self.run('build-snapshot') as (work, report):
            staging = work / 'build'
            staging.mkdir()
            for source in sources:
                shutil.copy2(source, staging / source.name)
            save(staging / MARKER, {'schema': 1, 'category': 'build', 'name': version,
                                  'created': time.time(), 'owner_pid': None, 'status': 'complete'})
            if destination.exists():
                remove(destination, self.builds)
            destination.parent.mkdir(parents=True, exist_ok=True)
            staging.rename(destination)
        self.clean(apply=True)
        return str(destination)

    def check(self):
        issues = []
        allowed = {
            '.tmp': {'runs', 'cache', 'slint-toolchain', 'slint-target', 'website-venv',
                     'release-runtime', 'uv-profile-cache', 'node-compile-cache'},
            '.local-dev': {'runtime', 'translation-bench', 'reports', 'archive', 'pnpm-store', 'deployments'},
            'dist': {'releases', 'release', 'replacement-backups', 'site', 'windows-metadata',
                     'zotero-pdf2zh-pro-windows-x64.zip', 'zotero-pdf2zh-pro-windows-x64-setup.exe',
                     'zotero-pdf2zh-pro-windows-x64-setup.exe.sha256', 'windows-update.json',
                     'replacement-source.json', 'replacement-backup-path.txt'},
        }
        for directory, names in allowed.items():
            parent = self.root / directory
            if linked(parent):
                issues.append(f'{directory}: output root is a link')
                continue
            if parent.exists():
                for path in parent.iterdir():
                    source = directory == 'dist' and re.fullmatch(r'zotero-pdf2zh-pro-\d+\.\d+\.\d+(?:[-.][0-9A-Za-z.-]+)?-source\.zip', path.name)
                    if path.name not in names and path.name != '.keep' and not source:
                        issues.append(f'{directory}/{path.name}: move to a managed run, report, archive or versioned build')
        for parent, category in ((self.runs, 'run'), (self.reports, 'report'), (self.builds, 'build')):
            if linked(parent):
                issues.append(f'{parent}: output root is a link')
                continue
            if parent.exists():
                for path in parent.iterdir():
                    if path.name == '.keep':
                        continue
                    try:
                        checked(path, parent)
                        metadata(path, category)
                    except (OSError, ValueError) as error:
                        issues.append(str(error))
        for path in self.root.iterdir():
            if path.is_file() and path.suffix.lower() in {'.log', '.tmp', '.zip', '.exe', '.pdf', '.py', '.png', '.jpg'}:
                issues.append(f'{path.name}: generated files do not belong at repository root')
        return issues


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('check')
    sub.add_parser('status')
    clean = sub.add_parser('clean')
    clean.add_argument('--apply', action='store_true', help='Delete listed managed outputs; default is preview.')
    for command in ('start', 'report'):
        create = sub.add_parser(command)
        create.add_argument('kind')
        create.add_argument('--owner-pid', type=int, default=os.getppid())
    finish = sub.add_parser('finish')
    finish.add_argument('work', type=Path)
    finish.add_argument('--status', choices=('passed', 'failed'), default='passed')
    snapshot = sub.add_parser('snapshot')
    snapshot.add_argument('version')
    snapshot.add_argument('files', nargs='+', type=Path)
    keep = sub.add_parser('keep', help='Protect a managed report, run or build from automatic cleanup.')
    keep.add_argument('path', type=Path)
    args = parser.parse_args()
    workspace = Workspace()
    if args.command == 'check':
        issues = workspace.check()
        print(json.dumps({'ok': not issues, 'issues': issues}, ensure_ascii=False, indent=2))
        return bool(issues)
    if args.command in ('clean', 'status'):
        result = {'apply': getattr(args, 'apply', False), 'candidates': workspace.clean(getattr(args, 'apply', False))}
        if args.command == 'status':
            result['issues'] = workspace.check()
    elif args.command == 'start':
        result = workspace.start(args.kind, args.owner_pid)
    elif args.command == 'report':
        result = {'report': str(workspace.create(args.kind, owner_pid=args.owner_pid))}
    elif args.command == 'finish':
        result = {'report': workspace.finish(args.work, args.status)}
    elif args.command == 'keep':
        path = args.path.absolute()
        parent, category = next(((parent, category) for parent, category in
                                 ((workspace.runs, 'run'), (workspace.reports, 'report'), (workspace.builds, 'build'))
                                 if path.parent == parent), (None, None))
        if parent is None:
            raise ValueError('Only a managed run, report or build can be marked for retention.')
        checked(path, parent)
        metadata(path, category)
        (path / '.keep').touch()
        result = {'kept': str(path)}
    else:
        result = {'build': workspace.snapshot(args.version, args.files)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        print(f'workspace: {error}', file=sys.stderr)
        raise SystemExit(1)
