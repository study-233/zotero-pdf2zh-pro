"""Build and validate locally, then import the exact candidate into Actions."""
import argparse
from contextlib import contextmanager, nullcontext
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import sys
import time
import uuid
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
import zipfile

import release_control as control
from check_release_artifacts import verify_source
from workspace import Workspace, measure, save

ROOT = control.ROOT
PYTHON = sys.executable


def run(arguments, environment=None):
    print('Running: ' + ' '.join(map(str, arguments)), file=sys.stderr, flush=True)
    operation = 'dependencies.install' if list(arguments[:3]) == ['uv', 'pip', 'install'] else 'command'
    with measure(operation, executable=str(arguments[0])):
        subprocess.run(list(map(str, arguments)), cwd=ROOT, env=environment, stdout=sys.stderr, check=True)


def powershell(script, *arguments):
    return [PYTHON, 'scripts/windows_powershell.py', 'scripts/' + script, *arguments]


def tool_environment():
    if os.name != 'nt':
        raise ValueError('Local release validation requires Windows; use --build-source actions')
    for tool in ('cargo', 'rustc', 'uv', 'node', 'npx', 'gh'):
        if not shutil.which(tool):
            raise ValueError(f'Missing local release tool: {tool}; use --build-source actions')
    environment = dict(os.environ, PYTHONUTF8='1', UV_DEFAULT_INDEX='https://pypi.org/simple')
    local = ROOT / '.tmp/slint-toolchain'
    environment.setdefault('LLVM_HOME', str(local / 'llvm'))
    environment.setdefault('SKIA_SOURCE_DIR', str(local / 'skia-source/skia-m153-0.101.2'))
    environment.setdefault('CARGO_TARGET_DIR', str(ROOT / '.tmp/slint-target'))
    if not (Path(environment['LLVM_HOME']) / 'bin/clang-cl.exe').is_file():
        raise ValueError('LLVM 21 is unavailable; prepare the build tools or use --build-source actions')
    if not (Path(environment['SKIA_SOURCE_DIR']) / 'BUILD.gn').is_file():
        raise ValueError('Skia source is unavailable; prepare the build tools or use --build-source actions')
    return environment


def cached_copy(source, destination, stats=None):
    source, destination = Path(source), Path(destination)
    if stats is not None:
        stats['files'] += 1
    if destination.exists():
        before, after = source.stat(), destination.stat()
        if (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns):
            return str(destination)
        destination.chmod(destination.stat().st_mode | stat.S_IWRITE)
    elif any(part.startswith('archive-v') for part in source.parts):
        # uv's extracted wheel files are immutable. Same-volume fixtures can share
        # them; removing an installation only removes its own hard links.
        try:
            os.link(source, destination)
            if stats is not None:
                stats['linkedFiles'] += 1
            return str(destination)
        except OSError:
            pass
    copied = shutil.copy2(source, destination)
    if stats is not None:
        stats['copiedBytes'] += destination.stat().st_size
    return copied


def seed_install(cache, root, parts=('cache', 'runtime/python', 'runtime/uv')):
    cache, root = Path(cache).resolve(), Path(root).resolve()
    temporary = (ROOT / '.tmp').resolve()
    if not cache.is_relative_to(temporary) or not root.is_relative_to(temporary) or root.is_relative_to(cache) or cache.is_relative_to(root):
        raise ValueError('Cache and installation must be separate isolated repository paths')
    lock = download_cache_lock(cache.parent.parent / (cache.name + '.lock')) if cache.parent.name == 'sets' else nullcontext()
    with lock, measure('cache.copy', source=str(cache), destination=str(root)) as stats:
        stats.update(files=0, linkedFiles=0, copiedBytes=0)
        def ignored(directory, names):
            return [name for name in names if name.endswith('.lock') or name.startswith(('.tmp', '.temp'))
                    or (Path(directory) / name).is_symlink() or (Path(directory) / name).is_junction()]
        for part in parts:
            if (cache / part).is_dir():
                shutil.copytree(cache / part, root / part, dirs_exist_ok=True,
                                copy_function=lambda source, target: cached_copy(source, target, stats),
                                ignore=ignored)


def download_spec(package):
    package = Path(package)
    if package.is_dir():
        manifest = json.loads((package / 'windows-dependencies.json').read_bytes())
        requirements = (package / 'windows-requirements.txt').read_bytes()
    else:
        with zipfile.ZipFile(package) as archive:
            manifest = json.loads(archive.read('windows-dependencies.json'))
            requirements = archive.read('windows-requirements.txt')
    if hashlib.sha256(requirements).hexdigest() != manifest['requirementsSha256']:
        raise ValueError('Download cache requirements differ from the final package')
    if not re.fullmatch(r'3\.13\.\d+', manifest['python']):
        raise ValueError('Expected an exact supported Windows Python version')
    return manifest, requirements


@contextmanager
def download_cache_lock(path):
    """Lock the seed while uv populates it; OS locks are released after crashes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as lock:
        if not path.stat().st_size:
            lock.write(b'0'); lock.flush()
        if os.name == 'nt':
            import msvcrt
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == 'nt':
                lock.seek(0); msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def download_inventory(specs):
    packages = {}
    for _, requirements in specs:
        for line in requirements.decode('utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith(('#', '--hash=', '\\')):
                continue
            match = re.match(r'([A-Za-z0-9_.-]+)(?:\[[^]]+\])?==([^\s;\\]+)', line)
            if not match:
                return None  # Unknown input formats still work, without speculative reuse.
            name = re.sub(r'[-_.]+', '-', match[1]).lower()
            packages.setdefault(name, set()).add(match[2])
    return {name: sorted(versions) for name, versions in packages.items()}


def reuse_downloads(base, cache, identity, packages, environment):
    seeded = cache / 'seeded-packages.json'
    if packages is not None and not (cache / 'cache').exists():
        candidates = []
        for source in (base / 'sets').iterdir():
            if source == cache:
                continue
            try:
                previous = json.loads((source / 'ready.json').read_text())
                inventory = json.loads((source / 'packages.json').read_text())
                if (previous['uvSha256'] != identity['uvSha256'] or previous['platform'] != identity['platform']
                        or {r[0] for r in previous['requirements']} != {r[0] for r in identity['requirements']}
                        or not isinstance(inventory, dict) or not inventory
                        or any(not re.fullmatch('[a-z0-9][a-z0-9-]*', n) or not isinstance(v, list)
                               or any(not isinstance(item, str) for item in v) for n, v in inventory.items())):
                    continue
            except (OSError, ValueError, KeyError, TypeError):
                continue
            distance = sum(len(set(inventory.get(n, [])) ^ set(packages.get(n, []))) for n in inventory.keys() | packages.keys())
            candidates.append((distance, source, inventory))
        if candidates:
            _, source, inventory = min(candidates, key=lambda item: item[0])
            save(seeded, inventory)
            print(f'Reusing unchanged downloads from {source.name[:12]}', file=sys.stderr, flush=True)
            seed_install(source, cache, parts=('cache',))
    if seeded.is_file():
        inventory = json.loads(seeded.read_text())
        obsolete = [n for n, versions in inventory.items() if set(versions) - set((packages or {}).get(n, []))]
        if obsolete:
            # Let uv update its own indices. The source snapshot remains unchanged.
            run(['uv', 'cache', 'clean', *sorted(obsolete)], environment)


def prepare_downloads(environment, plan=None, package=None):
    """Let uv create one target-specific cache, without merging unrelated user caches."""
    base = Path(environment.get('PDF2ZH_TEST_DOWNLOAD_CACHE', ROOT / '.tmp/cache/release-downloads')).resolve()
    if not base.is_relative_to((ROOT / '.tmp').resolve()):
        raise ValueError('Release download cache must stay inside the repository .tmp directory')
    if base.parent.name == 'sets' and re.fullmatch('[0-9a-f]{64}', base.name):
        base = base.parent.parent
    specs = [download_spec(package or ROOT / 'dist' / control.PACKAGE)]
    for baseline in (plan or {}).get('baselines', {}).values():
        source = control.cached_asset(control.REPO, baseline['package'], baseline['package']['sha256'])
        try:
            specs.append(download_spec(source))
        except KeyError:
            if baseline['version'] != control.policy()['legacyBaseline']:
                raise
            # The explicit legacy scenario retains its own frozen dependency setup.
    uv = Path(shutil.which('uv')).resolve()
    identity = {'schemaVersion': 1, 'platform': 'windows-x86_64', 'uvSha256': control.digest(uv),
                'requirements': sorted({(m['python'], m['requirementsSha256']) for m, _ in specs})}
    packages = download_inventory(specs)
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    cache = base / 'sets' / key
    marker = cache / 'ready.json'
    with download_cache_lock(base / (key + '.lock')):
        expected = json.loads(json.dumps(identity))
        python_ready = all((cache / 'runtime/python' / f'cpython-{m["python"]}-windows-x86_64-none/python.exe').is_file()
                           for m, _ in specs)
        uv_ready = (cache / 'runtime/uv/uv.exe').is_file() and control.digest(cache / 'runtime/uv/uv.exe') == identity['uvSha256']
        if marker.is_file() and json.loads(marker.read_text()) == expected and python_ready and uv_ready:
            save(cache / 'packages.json', packages)
            print(f'Reusing prepared download set {key[:12]}', file=sys.stderr, flush=True)
            environment['PDF2ZH_TEST_DOWNLOAD_CACHE'] = str(cache)
            return cache
        marker.unlink(missing_ok=True)
        seed_environment = dict(environment, UV_CACHE_DIR=str(cache / 'cache'),
                                UV_PYTHON_INSTALL_DIR=str(cache / 'runtime/python'), UV_DEFAULT_INDEX='https://pypi.org/simple')
        (cache / 'runtime/uv').mkdir(parents=True, exist_ok=True)
        shutil.copy2(uv, cache / 'runtime/uv/uv.exe')
        reuse_downloads(base, cache, identity, packages, seed_environment)
        with Workspace(ROOT).run('release-cache-prepare') as (work, _):
            for python_version in sorted({m['python'] for m, _ in specs}):
                try:
                    host = Path(sys.base_prefix).resolve()
                    if platform.python_version() == python_version and (host / 'BUILD').is_file():
                        interpreter = host / 'python.exe'
                    else:
                        interpreter = Path(control.command(['uv', 'python', 'find', python_version, '--managed-python']).decode().strip()).resolve()
                except RuntimeError:
                    run(['uv', 'python', 'install', python_version], seed_environment)
                else:
                    target = cache / 'runtime/python' / interpreter.parent.name
                    if interpreter.parent != target:
                        with measure('cache.python', version=python_version):
                            shutil.copytree(interpreter.parent, target, dirs_exist_ok=True)
            seen = set()
            for manifest, requirements in specs:
                spec = (manifest['python'], manifest['requirementsSha256'])
                if spec in seen:
                    continue
                seen.add(spec)
                required = work / f'{len(seen)}.txt'
                required.write_bytes(requirements)
                venv = work / 'runtime' / str(len(seen))
                run(['uv', 'venv', '--python', manifest['python'], '--managed-python', '--no-config', venv], seed_environment)
                run(['uv', 'pip', 'install', '--python', venv / 'Scripts/python.exe', '--no-config', '--no-deps',
                     '--require-hashes', '--link-mode', 'hardlink', '--default-index', 'https://pypi.org/simple', '-r', required], seed_environment)
        save(cache / 'packages.json', packages)
        save(marker, expected)
        (cache / 'seeded-packages.json').unlink(missing_ok=True)
    environment['PDF2ZH_TEST_DOWNLOAD_CACHE'] = str(cache)
    return cache


class Stages:
    def __init__(self, path, identity):
        self.path = Path(path)
        self.state = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {
            'identity': identity, 'id': uuid.uuid4().hex, 'stages': {}}
        if self.state['identity'] != identity:
            raise ValueError('Local source, plan or toolchain changed; start a new release state')
        save(self.path, self.state)

    def reusable(self, name, outputs=(), artifacts=None):
        outputs = [Path(p) for p in outputs]
        old = self.state['stages'].get(name, {})
        actual = {str(p): control.digest(p) for p in outputs if p.is_file()}
        return (old.get('status') == 'passed' and len(actual) == len(outputs)
                and old.get('outputs') == actual and old.get('artifacts') == artifacts)

    def execute(self, name, action, outputs=(), artifacts=None):
        outputs = [Path(p) for p in outputs]
        if self.reusable(name, outputs, artifacts):
            print(f'Reusing passed local stage: {name}', file=sys.stderr, flush=True)
            return
        started = time.time()
        self.state['stages'][name] = {'status': 'running', 'started': started}
        save(self.path, self.state)
        try:
            action()
            if not all(p.is_file() for p in outputs):
                raise ValueError(f'Missing output after {name}')
        except BaseException:
            self.state['stages'][name].update(status='failed', seconds=time.time() - started)
            save(self.path, self.state)
            raise
        self.state['stages'][name].update(status='passed', seconds=time.time() - started,
            outputs={str(p): control.digest(p) for p in outputs}, artifacts=artifacts)
        save(self.path, self.state)


def build(value, commit, plan_path, state_path):
    verify_source(value, commit)
    plan = json.loads(Path(plan_path).read_text(encoding='utf-8-sig'))
    control.validate_plan(plan)
    if plan['sourceCommit'] != commit or plan['harnessSha256'] != control.harness_digest(ROOT):
        raise ValueError('Local validation plan differs from the source or harness')
    os.environ.setdefault('PDF2ZH_RELEASE_METRICS_DIR', str(Path(state_path).resolve().parent / 'metrics'))
    environment = tool_environment()
    rustc = control.command(['rustc', '+stable-x86_64-pc-windows-msvc', '-Vv']).decode().strip()
    stages = Stages(state_path, {'commit': commit, 'plan': control.plan_digest(plan), 'rustc': rustc,
        'python': platform.python_version(), 'uv': control.command(['uv', '--version']).decode().strip(),
        'clang': control.command([str(Path(environment['LLVM_HOME']) / 'bin/clang-cl.exe'), '--version']).decode().strip(),
        'target': environment['CARGO_TARGET_DIR'], 'llvm': environment['LLVM_HOME'], 'skia': environment['SKIA_SOURCE_DIR']})
    invoke = lambda args: run(args, environment)
    script = lambda name, *args: invoke([PYTHON, 'scripts/' + name, *args])
    wheel = ROOT / f'server/dist/zotero_pdf2zh_pro-{value}-py3-none-any.whl'
    sdist = wheel.with_name(f'zotero_pdf2zh_pro-{value}.tar.gz')
    product = 'zotero-pdf2zh-pro'
    binary = Path(environment['CARGO_TARGET_DIR']) / f'x86_64-pc-windows-msvc/release/{product}.exe'
    bootstrap = binary.with_name(f'{product}-setup.exe')
    def python_packages():
        invoke(['uv', 'build', 'server', '--out-dir', 'server/dist', '--clear', '--no-sources'])
        script('check_pypi_artifacts.py', 'server/dist', value)
    stages.execute('python-build', python_packages, [wheel, sdist])
    def plugin():
        # npx.cmd requires the Windows command processor; no user text is interpolated.
        run(['cmd.exe', '/d', '/c', 'npx.cmd', '--yes', 'pnpm@10.34.5', '--dir', 'plugin', 'install', '--frozen-lockfile',
             '--store-dir', str(ROOT / '.local-dev/pnpm-store')], dict(environment, CI='true'))
        invoke(['cmd.exe', '/d', '/c', 'npx.cmd', '--yes', 'pnpm@10.34.5', '--dir', 'plugin', 'build'])
    stages.execute('plugin-build', plugin, [ROOT / f'plugin/build/{product}.xpi', ROOT / 'plugin/build/update.json'])
    stages.execute('native-build', lambda: invoke(powershell('build_windows_control.ps1')),
                   [binary, bootstrap, Path(environment['CARGO_TARGET_DIR']) / 'THIRD_PARTY_NOTICES.md'])
    package = ROOT / f'dist/{product}-windows-x64.zip'
    installer = ROOT / f'dist/{product}-windows-x64-setup.exe'
    update = ROOT / 'dist/windows-update.json'
    def packages():
        script('build_windows_package.py', '--version', value, '--gui-binary', binary)
        script('build_windows_installer.py', '--version', value, '--bootstrap-binary', bootstrap)
        script('build_windows_update_manifest.py', '--version', value, '--package', package, '--output', update)
        script('test_windows_installer.py')
        script('test_windows_update_manifest.py')
    stages.execute('package-build', packages, [package, installer, update], {wheel.name: control.digest(wheel), binary.name: control.digest(binary), bootstrap.name: control.digest(bootstrap)})
    source = ROOT / f'dist/{product}-{value}-source.zip'
    stages.execute('source', lambda: invoke(['git', 'archive', '--format=zip', f'--prefix={product}-{value}/', f'--output={source}', commit]), [source])
    artifacts = control.product_identity(value)
    # All test stages bind the complete final artifact set, including the source archive.
    stages.execute('native-tests', lambda: invoke(powershell('build_windows_control.ps1', '-RunTests')), artifacts=artifacts)
    stages.execute('package-window', lambda: invoke(powershell('test_windows_package.ps1', '-Package', str(package))), artifacts=artifacts)
    validate_local(stages, environment, plan, plan_path, artifacts, value, commit, package, installer, wheel)
    cases = Path(state_path).with_name('local-upgrades.json')
    receipt_path = Path(state_path).with_name('local-standard-validation.json')
    receipt = {'schemaVersion': 2, 'status': 'passed', 'plan': plan, 'planSha256': control.plan_digest(plan),
        'lifecycle': plan['lifecycle'], 'artifacts': artifacts, 'stages': stages.state['stages'],
        'cases': json.loads(cases.read_text(encoding='utf-8-sig'))['cases'],
        'execution': {'type': 'local', 'system': platform.system(), 'id': stages.state['id'],
            'machine': platform.machine(), 'python': platform.python_version(), 'rustc': rustc}}
    control.verify_standard_receipt(receipt, plan, artifacts)
    save(receipt_path, receipt)
    script('check_release_artifacts.py', value, commit, '--collect')
    control.attach_plan(plan_path, ROOT / 'dist/release/checksums.json', standard_receipt=receipt_path)
    control.verify_publication_contract(ROOT / 'dist/release')
    return stages


def validate_local(stages, environment, plan, plan_path, artifacts, value, commit, package, installer, wheel):
    product = 'zotero-pdf2zh-pro'
    cases = stages.path.with_name('local-upgrades.json')
    if all(stages.reusable(name, [cases] if name == 'upgrades' else [], artifacts)
           for name in ('runtime', 'lifecycle', 'progress', 'upgrades')):
        print('Reusing all local validation stages; no cache preparation or test workspace needed.', file=sys.stderr, flush=True)
        return
    invoke = lambda args: run(args, environment)
    script = lambda name, *args: invoke([PYTHON, 'scripts/' + name, *args])
    with Workspace(ROOT).run('local-release-validation') as (work, report):
        environment.update(RUNNER_TEMP=str(work), TEMP=str(work / 'temp'), TMP=str(work / 'temp'),
            PDF2ZH_DATA_DIR=str(work / 'data'), PDF2ZH_WINDOWS_APP_ROOT=str(work / 'installation 中文 with spaces'),
            PDF2ZH_WINDOWS_REGISTRY_KEY='HKCU:\\Software\\pdf2zh-local-release-' + stages.state['id'],
            PDF2ZH_WINDOWS_START_MENU_DIR=str(work / 'menu'), PDF2ZH_WINDOWS_PORT='18890',
            UV_TOOL_DIR=str(work / 'uv-tools'), UV_TOOL_BIN_DIR=str(work / 'uv-bin'),
            PDF2ZH_VALIDATION_COMMIT=commit, PDF2ZH_VALIDATION_PLAN=str(Path(plan_path).resolve()),
            PDF2ZH_TEST_INSTALLER=str(installer))
        (work / 'temp').mkdir()
        def runtime():
            venv = work / 'runtime'
            invoke(['uv', 'venv', '--python', '3.13', venv])
            python = venv / 'Scripts/python.exe'
            invoke(['uv', 'pip', 'install', '--python', python, wheel])
            invoke([python, 'scripts/check_installed_runtime.py', value])
        stages.execute('runtime', runtime, artifacts=artifacts)
        lifecycle = powershell('test_windows_lifecycle.ps1', '-WindowsPackage', str(package), '-PackageSource', str(wheel))
        if plan['lifecycle'] == 'standard':
            lifecycle.append('-SmokeOnly')
        cache = None
        def downloads():
            nonlocal cache
            if cache is None:
                with measure('cache.prepare'):
                    cache = prepare_downloads(environment, plan)
            return cache
        def installed_lifecycle():
            root = Path(environment['PDF2ZH_WINDOWS_APP_ROOT'])
            download_set = downloads()
            with measure('cache.seed', root=str(root)):
                seed_install(download_set, root)
            environment['PDF2ZH_WINDOWS_LIFECYCLE_PREPARED_CACHE'] = '1'
            invoke(lifecycle)
        stages.execute('lifecycle', installed_lifecycle, artifacts=artifacts)
        def progress():
            unpacked = work / 'progress'
            with zipfile.ZipFile(package) as archive:
                archive.extractall(unpacked)
            environment.update(PDF2ZH_TEST_CONTROL=str(unpacked / f'{product}.exe'), PDF2ZH_TEST_UV=shutil.which('uv'),
                PDF2ZH_TEST_PYTHON=control.command(['uv', 'python', 'find', '3.13']).decode().strip(), PDF2ZH_TEST_VERSION=value)
            script('test_windows_uv_progress.py')
        stages.execute('progress', progress, artifacts=artifacts)
        def upgrades():
            downloads()
            invoke(powershell('test_windows_release_extended.ps1', '-Version', value,
                '-Plan', str(Path(plan_path).resolve()), '-Receipt', str(cases), '-SkipLifecycle'))
        stages.execute('upgrades', upgrades, [cases], artifacts=artifacts)


def candidate_release(tag, release_id=None):
    # The tag endpoint omits drafts. Resolve their ID from the authenticated list.
    if release_id:
        return control.api(f'repos/{control.REPO}/releases/{release_id}')
    pages = json.loads(control.command(['gh', 'api', '--paginate', '--slurp',
                                      f'repos/{control.REPO}/releases?per_page=100']))
    found = [r for page in pages for r in page if r['tag_name'] == tag]
    if len(found) > 1:
        raise ValueError('Ambiguous candidate release')
    if not found:
        raise RuntimeError('Candidate not found (HTTP 404)')
    return found[0]


def stage_candidate(stages, value, commit):
    """Authenticated draft assets are transport; the receiving workflow verifies all bytes."""
    directory = ROOT / 'dist/release'
    bundle = stages.path.with_name('local-candidate.zip')
    names = {Path(p).name for p in control.product_paths(value)} | {'checksums.json', 'validation-plan.json', 'standard-validation.json'}
    with zipfile.ZipFile(bundle, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(names):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, (directory / name).read_bytes())
    sha = control.digest(bundle)
    candidate = stages.state.setdefault('candidate', {'tag': f'candidate-v{value}-{stages.state["id"]}'})
    save(stages.path, stages.state)
    tag = candidate['tag']
    try:
        release = candidate_release(tag, candidate.get('releaseId'))
    except RuntimeError as error:
        if 'HTTP 404' not in str(error):
            raise
        notes = stages.path.with_name('candidate-notes.txt')
        notes.write_text('Local release candidate and validation evidence; pending import into Actions.\n', encoding='utf-8')
        run(['gh', 'release', 'create', tag, '--repo', control.REPO, '--draft', '--latest=false', '--target', commit,
            '--title', f'Candidate v{value}', '--notes-file', notes])
        release = candidate_release(tag)
    if not release['draft'] or release['target_commitish'] != commit:
        raise ValueError('Candidate release identity changed')
    candidate['releaseId'] = release['id']
    save(stages.path, stages.state)
    found = [a for a in release['assets'] if a['name'] == bundle.name]
    if not found:
        run(['gh', 'release', 'upload', tag, str(bundle), '--repo', control.REPO])
        release = candidate_release(tag, release['id'])
        found = [a for a in release['assets'] if a['name'] == bundle.name]
    if len(found) != 1 or found[0].get('digest') != 'sha256:' + sha:
        raise ValueError('Candidate upload differs; preserve it and inspect before resuming')
    candidate.update(releaseId=release['id'], assetId=found[0]['id'], sha256=sha)
    save(stages.path, stages.state)
    return candidate


def signed_candidate_url(asset_id):
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    token = control.command(['gh', 'auth', 'token']).decode().strip()
    request = Request(f'https://api.github.com/repos/{control.REPO}/releases/assets/{asset_id}',
                      headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/octet-stream'})
    try:
        with build_opener(NoRedirect).open(request, timeout=30):
            raise ValueError('GitHub did not provide a temporary candidate download URL')
    except HTTPError as error:
        if error.code not in (302, 307):
            raise
        url = error.headers['Location']
    validate_candidate_url(url)
    return url


def validate_candidate_url(url):
    parsed = urlparse(url)
    if (any(c in url for c in '\r\n') or parsed.scheme != 'https' or not (parsed.hostname or '').endswith('.githubusercontent.com')
            or parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise ValueError('Candidate URL must be a GitHub HTTPS asset download')


def import_candidate(asset_id, sha, value, commit, destination, url=None):
    if not re.fullmatch('[1-9][0-9]*', asset_id) or not re.fullmatch('[0-9a-f]{64}', sha):
        raise ValueError('Invalid candidate asset identity')
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    bundle = destination.parent / 'local-candidate.zip'
    if url:
        validate_candidate_url(url)
        control.download(['curl', '--fail', '--silent', '--show-error', '--proto', '=https', url], bundle)
    else:
        asset = control.api(f'repos/{control.REPO}/releases/assets/{asset_id}')
        if asset['name'] != bundle.name or asset.get('digest') != 'sha256:' + sha:
            raise ValueError('Candidate asset digest mismatch')
        control.download(['gh', 'api', '-H', 'Accept: application/octet-stream',
                          f'repos/{control.REPO}/releases/assets/{asset_id}'], bundle)
    if control.digest(bundle) != sha:
        raise ValueError('Candidate download digest mismatch')
    names = {Path(p).name for p in control.product_paths(value)} | {'checksums.json', 'validation-plan.json', 'standard-validation.json'}
    with zipfile.ZipFile(bundle) as archive:
        if set(archive.namelist()) != names or len(archive.namelist()) != len(names) or archive.testzip():
            raise ValueError('Unexpected candidate contents')
        archive.extractall(destination)
    manifest = json.loads((destination / 'checksums.json').read_text())
    if manifest['version'] != value or manifest['commit'] != commit or manifest.get('replacement'):
        raise ValueError('Candidate source identity mismatch')
    control.verify_publication_contract(destination)
    for name, identity in manifest['artifacts'].items():
        file = destination / name
        if file.stat().st_size != identity['size'] or control.digest(file) != identity['sha256']:
            raise ValueError(f'Candidate bytes changed: {name}')
    receipt = json.loads((destination / 'standard-validation.json').read_text())
    if receipt.get('schemaVersion') != 2:
        raise ValueError('Imported candidates require local validation evidence')
    for relative in control.product_paths(value):
        target = ROOT / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(destination / target.name, target)
    # Independently inspect package contents/versions against the checked-out source.
    run([PYTHON, 'scripts/check_pypi_artifacts.py', 'server/dist', value])
    run([PYTHON, 'scripts/check_release_artifacts.py', value, commit, '--collect'])
    current = json.loads((ROOT / 'dist/release/checksums.json').read_text())
    if current['artifacts'] != manifest['artifacts']:
        raise ValueError('Collected candidate identity differs')
    control.attach_plan(destination / 'validation-plan.json', ROOT / 'dist/release/checksums.json',
                        standard_receipt=destination / 'standard-validation.json')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('preflight')
    seed = sub.add_parser('seed-cache')
    seed.add_argument('cache'); seed.add_argument('root')
    prepare = sub.add_parser('prepare-cache')
    prepare.add_argument('package', type=Path)
    prepare.add_argument('--plan', type=Path)
    local = sub.add_parser('build')
    local.add_argument('version'); local.add_argument('commit'); local.add_argument('plan', type=Path)
    local.add_argument('state', type=Path); local.add_argument('--upload', action='store_true')
    receive = sub.add_parser('import')
    receive.add_argument('--url')
    receive.add_argument('--url-from-event', action='store_true')
    for name in ('asset', 'sha256', 'version', 'commit', 'destination'):
        receive.add_argument(name)
    args = parser.parse_args()
    if args.action == 'preflight':
        tool_environment()
    elif args.action == 'seed-cache':
        seed_install(args.cache, args.root)
    elif args.action == 'prepare-cache':
        plan = json.loads(args.plan.read_text(encoding='utf-8-sig')) if args.plan else None
        print(prepare_downloads(dict(os.environ), plan, args.package))
    elif args.action == 'build':
        stages = build(args.version, args.commit, args.plan, args.state)
        if args.upload:
            print(json.dumps(stage_candidate(stages, args.version, args.commit)))
    else:
        url = args.url
        if args.url_from_event:
            event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
            url = event['inputs']['candidate_url']
            validate_candidate_url(url)
            print('::add-mask::' + url, flush=True)
        import_candidate(args.asset, args.sha256, args.version, args.commit, args.destination, url)


if __name__ == '__main__':
    main()
