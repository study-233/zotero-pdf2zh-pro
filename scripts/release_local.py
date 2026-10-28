"""Build and validate locally, then import the exact candidate into Actions."""
import argparse
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
import tomllib
import uuid
import zipfile

import release_control as control
from check_release_artifacts import verify_source
from workspace import Workspace, save

ROOT = control.ROOT
PYTHON = sys.executable


def run(arguments, environment=None):
    print('Running: ' + ' '.join(map(str, arguments)), file=sys.stderr, flush=True)
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


def cached_copy(source, destination):
    source, destination = Path(source), Path(destination)
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
            return str(destination)
        except OSError:
            pass
    return shutil.copy2(source, destination)


def copy_downloads(source, destination, required):
    source, destination = Path(source), Path(destination)
    metadata = {f'{name}-{version}.dist-info'.lower().replace('-', '_') for name, version in required}
    for bucket in source.glob('archive-v*'):
        for archive in bucket.iterdir():
            if archive.is_dir() and any(p.name.lower().replace('-', '_') in metadata for p in archive.iterdir()):
                shutil.copytree(archive, destination / bucket.name / archive.name, dirs_exist_ok=True, copy_function=cached_copy)
    versions = {}
    for name, version in required:
        versions.setdefault(name.replace('-', '_'), set()).add(version)
    for bucket in source.glob('wheels-v*'):
        pypi = bucket / 'pypi'
        if pypi.is_dir():
            for package in pypi.iterdir():
                for item in package.iterdir() if package.is_dir() else ():
                    if item.name.endswith('.lock') or not any(item.name.startswith(v + '-') for v in versions.get(package.name.replace('-', '_'), ())):
                        continue
                    target = destination / bucket.name / 'pypi' / package.name / item.name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if item.is_dir():
                        shutil.copytree(item, target, dirs_exist_ok=True, copy_function=cached_copy)
                    else:
                        cached_copy(item, target)
        if (bucket / 'url').exists():
            shutil.copytree(bucket / 'url', destination / bucket.name / 'url', dirs_exist_ok=True,
                            copy_function=cached_copy, ignore=shutil.ignore_patterns('*.lock', '.tmp*'))
    for bucket in source.glob('simple-v*'):
        shutil.copytree(bucket, destination / bucket.name, dirs_exist_ok=True,
                        copy_function=cached_copy, ignore=shutil.ignore_patterns('*.lock', '.tmp*'))


def seed_install(cache, root):
    cache, root = Path(cache).resolve(), Path(root).resolve()
    temporary = (ROOT / '.tmp').resolve()
    if not cache.is_relative_to(temporary) or not root.is_relative_to(temporary) or root.is_relative_to(cache) or cache.is_relative_to(root):
        raise ValueError('Cache and installation must be separate isolated repository paths')
    for part in ('cache', 'runtime/python', 'runtime/uv'):
        if (cache / part).is_dir():
            shutil.copytree(cache / part, root / part, dirs_exist_ok=True, copy_function=cached_copy,
                            ignore=shutil.ignore_patterns('*.lock', '.tmp*'))


def prepare_downloads(environment, plan=None):
    """Seed isolated installs with downloaded bytes; installers still verify the lock hashes."""
    cache = Path(environment.get('PDF2ZH_TEST_DOWNLOAD_CACHE', ROOT / '.tmp/cache/release-downloads')).resolve()
    if not cache.is_relative_to((ROOT / '.tmp').resolve()):
        raise ValueError('Release download cache must stay inside the repository .tmp directory')
    packages = tomllib.loads((ROOT / 'server/uv.lock').read_text(encoding='utf-8'))['package']
    for baseline in (plan or {}).get('baselines', {}):
        packages += tomllib.loads(control.command(['git', 'show', f'v{baseline}:server/uv.lock']).decode())['package']
    required = {(p['name'], p['version']) for p in packages}
    print(f'Preparing download cache for {len(required)} current/baseline package versions', file=sys.stderr, flush=True)
    source = Path(control.command(['uv', 'cache', 'dir']).decode().strip())
    old = ROOT / '.tmp/cache/release-test-downloads'
    for origin in (old / 'cache', source):
        if origin.is_dir() and origin.resolve() != (cache / 'cache').resolve():
            copy_downloads(origin, cache / 'cache', required)
    if (old / 'runtime/python').is_dir():
        shutil.copytree(old / 'runtime/python', cache / 'runtime/python', dirs_exist_ok=True, copy_function=cached_copy)
    python_root = Path(control.command(['uv', 'python', 'dir']).decode().strip())
    for python in python_root.glob('cpython-3.13*-windows-x86_64-none'):
        target = cache / 'runtime/python' / python.name
        shutil.copytree(python, target, dirs_exist_ok=True, copy_function=cached_copy)
    (cache / 'runtime/uv').mkdir(parents=True, exist_ok=True)
    cached_copy(shutil.which('uv'), cache / 'runtime/uv/uv.exe')
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

    def execute(self, name, action, outputs=(), artifacts=None):
        outputs = [Path(p) for p in outputs]
        old = self.state['stages'].get(name, {})
        actual = {str(p): control.digest(p) for p in outputs if p.is_file()}
        if (old.get('status') == 'passed' and len(actual) == len(outputs)
                and old.get('outputs') == actual and old.get('artifacts') == artifacts):
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
        cache = prepare_downloads(environment, plan)
        def installed_lifecycle():
            root = Path(environment['PDF2ZH_WINDOWS_APP_ROOT'])
            seed_install(cache, root)
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
        cases = Path(state_path).with_name('local-upgrades.json')
        stages.execute('upgrades', lambda: invoke(powershell('test_windows_release_extended.ps1', '-Version', value,
            '-Plan', str(Path(plan_path).resolve()), '-Receipt', str(cases), '-SkipLifecycle')),
            [cases], artifacts=artifacts)
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


def import_candidate(asset_id, sha, value, commit, destination):
    if not re.fullmatch('[1-9][0-9]*', asset_id) or not re.fullmatch('[0-9a-f]{64}', sha):
        raise ValueError('Invalid candidate asset identity')
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    bundle = destination.parent / 'local-candidate.zip'
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
    local = sub.add_parser('build')
    local.add_argument('version'); local.add_argument('commit'); local.add_argument('plan', type=Path)
    local.add_argument('state', type=Path); local.add_argument('--upload', action='store_true')
    receive = sub.add_parser('import')
    for name in ('asset', 'sha256', 'version', 'commit', 'destination'):
        receive.add_argument(name)
    args = parser.parse_args()
    if args.action == 'preflight':
        tool_environment()
    elif args.action == 'seed-cache':
        seed_install(args.cache, args.root)
    elif args.action == 'build':
        stages = build(args.version, args.commit, args.plan, args.state)
        if args.upload:
            print(json.dumps(stage_candidate(stages, args.version, args.commit)))
    else:
        import_candidate(args.asset, args.sha256, args.version, args.commit, args.destination)


if __name__ == '__main__':
    main()
