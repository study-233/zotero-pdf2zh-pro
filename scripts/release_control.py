"""Bounded, resumable release coordination. No publication occurs without an explicit command."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen
import uuid

from release_policy import changed_paths, harness_digest, policy, policy_digest, previous_release, select, version
from workspace import Workspace, alive, save

ROOT = Path(os.environ.get('PDF2ZH_RELEASE_ROOT', Path(__file__).resolve().parents[1])).resolve()
REPO = 'study-233/zotero-pdf2zh-pro'
PACKAGE = 'zotero-pdf2zh-pro-windows-x64.zip'


def command(args, timeout=60):
    result = subprocess.run(args, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(result.stderr.decode('utf-8', 'replace').strip())
    return result.stdout


def api(endpoint):
    return json.loads(command(['gh', 'api', endpoint]))


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def download(args, destination, timeout=1800, stall=180):
    """Bound both elapsed time and lack of bytes; never block on an unread stderr pipe."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile() as errors, destination.open('wb') as output:
        process = subprocess.Popen(args, stdout=output, stderr=errors)
        started = progressed = time.monotonic()
        size = 0
        try:
            while process.poll() is None:
                now = time.monotonic()
                current = destination.stat().st_size
                if current != size:
                    size, progressed = current, now
                if now - started > timeout or now - progressed > stall:
                    raise TimeoutError('Download exceeded elapsed/no-progress limit; resume this stage')
                time.sleep(0.25)
            if process.returncode:
                errors.seek(0)
                raise RuntimeError(errors.read().decode('utf-8', 'replace')[-4000:])
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


def asset_bytes(repo, asset):
    return command(['gh', 'api', f'repos/{repo}/releases/assets/{asset["id"]}',
                    '-H', 'Accept: application/octet-stream'])


def cached_asset(repo, asset, expected=None):
    """Pin the first official historical download if GitHub has no recorded digest."""
    recorded = asset.get('digest') or ''
    expected = expected or (recorded.removeprefix('sha256:') if recorded.startswith('sha256:') else None)
    if expected and not re.fullmatch('[0-9a-f]{64}', expected):
        raise ValueError('Invalid asset digest')
    cache = ROOT / '.tmp/cache/release-baselines'
    if expected:
        target = cache / expected
        if target.is_file() and target.stat().st_size == asset['size'] and digest(target) == expected:
            return target
    with Workspace(ROOT).run('baseline-download') as (work, report):
        path = work / 'asset.part'
        download(['gh', 'api', f'repos/{repo}/releases/assets/{asset["id"]}',
                  '-H', 'Accept: application/octet-stream'], path)
        actual = digest(path)
        if path.stat().st_size != asset['size'] or (expected and actual != expected):
            raise ValueError('Published baseline asset differs from its pinned identity')
        cache.mkdir(parents=True, exist_ok=True)
        target = cache / actual
        os.replace(path, target)
        save(report / 'download.json', {'assetId': asset['id'], 'sha256': actual, 'size': asset['size']})
        return target


def tag_commit(repo, tag):
    obj = api(f'repos/{repo}/git/ref/tags/{tag}')['object']
    for _ in range(10):
        if obj['type'] == 'commit':
            return obj['sha']
        if obj['type'] != 'tag':
            break
        obj = api(f'repos/{repo}/git/tags/{obj["sha"]}')['object']
    raise ValueError('Release tag does not resolve to a commit')


def freeze_baseline(repo, value, release=None):
    version(value)
    tag = 'v' + value
    release = release or api(f'repos/{repo}/releases/tags/{tag}')
    if release['draft'] or release['prerelease'] or release['tag_name'] != tag:
        raise ValueError('Baseline must be a published stable release')
    packages = [a for a in release['assets'] if a['name'] == PACKAGE]
    if len(packages) != 1:
        raise ValueError(f'Missing/ambiguous baseline package: {tag}')
    package = packages[0]
    expected = None
    manifests = [a for a in release['assets'] if a['name'] == 'windows-update.json']
    if manifests:
        if len(manifests) != 1:
            raise ValueError('Ambiguous Windows manifest')
        manifest = json.loads(asset_bytes(repo, manifests[0]))
        if manifest['schemaVersion'] != 1 or manifest['version'] != value or manifest['size'] != package['size']:
            raise ValueError('Baseline manifest identity mismatch')
        expected = manifest['sha256']
    cached = cached_asset(repo, package, expected)
    with urlopen(f'https://pypi.org/pypi/zotero-pdf2zh-pro/{value}/json', timeout=30) as response:
        metadata = json.load(response)
    wheels = [f for f in metadata['urls'] if f['filename'] == f'zotero_pdf2zh_pro-{value}-py3-none-any.whl']
    if len(wheels) != 1:
        raise ValueError('Expected one published baseline wheel')
    wheel = wheels[0]
    baseline = {'version': value, 'commit': tag_commit(repo, tag), 'releaseId': release['id'],
            'package': {'id': package['id'], 'size': package['size'], 'sha256': digest(cached)},
            'wheel': {'name': wheel['filename'], 'size': wheel['size'],
                      'sha256': wheel['digests']['sha256'], 'url': wheel['url']}}
    if value == policy()['legacyBaseline']:
        baseline['constraints'] = legacy_constraints(baseline['commit'])
        baseline['constraintsSha256'] = hashlib.sha256(baseline['constraints'].encode()).hexdigest()
    return baseline


def legacy_constraints(commit):
    with Workspace(ROOT).run('legacy-constraints') as (work, _):
        for name in ('pyproject.toml', 'uv.lock'):
            (work/name).write_bytes(command(['git','-C',str(ROOT),'show',f'{commit}:server/{name}']))
        requirements = command(['uv','export','--frozen','--no-dev','--no-emit-project','--no-hashes','--no-header','--no-annotate',
                                '--project',str(work)]).decode('utf-8')
        return requirements + '\n' + '\n'.join(policy()['legacyConstraints']) + '\n'


def legacy_uv(destination):
    """Prepare the pinned fixture tool; never run the old floating bootstrap URL."""
    expected=policy()['legacyUv']
    destination=Path(destination)
    destination.parent.mkdir(parents=True,exist_ok=True)
    existing=shutil.which('uv.exe')
    if existing and digest(existing)==expected['sha256']:
        shutil.copyfile(existing,destination)
    else:
        with Workspace(ROOT).run('legacy-uv') as (work,_):
            archive=work/'uv.zip'
            download(['curl','--fail','--location','--silent','--show-error','--connect-timeout','30',
                      '--max-time','600','--speed-time','60','--speed-limit','1',
                      f'https://github.com/astral-sh/uv/releases/download/{expected["version"]}/uv-x86_64-pc-windows-msvc.zip'],archive)
            import zipfile
            with zipfile.ZipFile(archive) as zipped:
                names=[name for name in zipped.namelist() if Path(name).name=='uv.exe']
                if len(names)!=1: raise ValueError('Pinned uv archive has no unique executable')
                destination.write_bytes(zipped.read(names[0]))
    if digest(destination)!=expected['sha256']:
        destination.unlink(missing_ok=True)
        raise ValueError('Historical uv binary identity mismatch')


def make_plan(value, commit, output, full=False, compatibility=False):
    version(value)
    if not re.fullmatch('[0-9a-f]{40}',commit):
        raise ValueError('Validation plan requires the full product commit')
    output = Path(output)
    if output.exists():
        plan = json.loads(output.read_text(encoding='utf-8-sig'))
        if plan['version'] != value or plan['sourceCommit'] != commit:
            raise ValueError('Existing validation plan belongs to another release')
        if plan['policySha256'] != policy_digest():
            raise ValueError('Validation policy changed; create a new reviewed plan')
        if (full or compatibility) and not plan['fullValidation']:
            raise ValueError('Existing plan omits requested compatibility checks')
        validate_plan(plan)
        if plan.get('harnessSha256') != harness_digest(ROOT):
            raise ValueError('Validation harness changed; create a new plan and revalidate existing artifacts')
        return plan
    pages = json.loads(command(['gh', 'api', '--paginate', '--slurp', f'repos/{REPO}/releases?per_page=100']))
    previous = previous_release([r for page in pages for r in page], value)
    old = tag_commit(REPO, previous['tag_name'])
    changes = changed_paths(ROOT, old, commit)
    recent_compatibility = compatibility_result()
    retry_compatibility = bool(recent_compatibility and recent_compatibility['conclusion'] != 'success')
    plan = select(changes, previous['tag_name'].removeprefix('v'), value, full, compatibility or retry_compatibility)
    plan['compatibilityRun'] = recent_compatibility
    baselines = {case['baseline'] for case in plan['cases']}
    plan.update(sourceCommit=commit, harnessSha256=harness_digest(ROOT), baselines={})
    for baseline in sorted(baselines, key=version):
        plan['baselines'][baseline] = freeze_baseline(REPO, baseline, previous if baseline == plan['previousVersion'] else None)
    output.parent.mkdir(parents=True, exist_ok=True)
    save(output, plan)
    return plan


def compatibility_result():
    try:
        runs = api(f'repos/{REPO}/actions/workflows/windows-compatibility.yml/runs?status=completed&per_page=1')['workflow_runs']
    except RuntimeError as error:
        if 'HTTP 404' in str(error):
            return None  # The new workflow has not reached the default branch yet.
        raise
    return {key:runs[0][key] for key in ('id','head_sha','conclusion')} if runs else None


def validate_plan(plan):
    """Recompute required coverage without changing an already frozen previous release."""
    if plan['schemaVersion'] != 2 or version(plan['previousVersion']) >= version(plan['version']):
        raise ValueError('Invalid validation plan versions')
    if not re.fullmatch('[0-9a-f]{64}',plan.get('harnessSha256','')):
        raise ValueError('Validation plan has no fixed test harness identity')
    if plan['policySha256'] != policy_digest():
        raise ValueError('Validation policy changed; revalidate the artifacts')
    for value, baseline in plan['baselines'].items():
        actual = command(['git', '-C', str(ROOT), 'rev-parse', f'v{value}^{{commit}}']).decode().strip()
        if baseline['version'] != value or baseline['commit'] != actual:
            raise ValueError('Frozen baseline source does not match its release tag')
        for asset in (baseline['package'], baseline['wheel']):
            if asset['size'] <= 0 or not re.fullmatch('[0-9a-f]{64}', asset['sha256']):
                raise ValueError('Invalid frozen asset identity')
        wheel = baseline['wheel']
        if (wheel['name'] != f'zotero_pdf2zh_pro-{value}-py3-none-any.whl'
                or not wheel['url'].startswith('https://files.pythonhosted.org/')):
            raise ValueError('Baseline wheel must be an official PyPI file')
        if value == policy()['legacyBaseline']:
            if hashlib.sha256(baseline['constraints'].encode()).hexdigest() != baseline['constraintsSha256']:
                raise ValueError('Historical dependency constraints changed')
    previous = plan['baselines'][plan['previousVersion']]
    required = select(changed_paths(ROOT, previous['commit'], plan['sourceCommit']),
                      plan['previousVersion'], plan['version'], full=plan['fullValidation'])
    if any(plan.get(key) != required[key] for key in ('cases', 'fullValidation', 'extendedValidation', 'lifecycle')):
        raise ValueError('Validation plan omits required upgrade coverage')
    if set(plan['baselines']) != {case['baseline'] for case in required['cases']}:
        raise ValueError('Validation plan baselines do not match its cases')


def fetch_baseline(plan, value, destination):
    baseline = plan['baselines'][value]
    package = cached_asset(REPO, baseline['package'], baseline['package']['sha256'])
    wheel = baseline['wheel']
    if (wheel['name'] != f'zotero_pdf2zh_pro-{value}-py3-none-any.whl'
            or not wheel['url'].startswith('https://files.pythonhosted.org/')
            or not re.fullmatch('[0-9a-f]{64}',wheel['sha256'])):
        raise ValueError('Baseline wheel must be an official PyPI file with the expected filename')
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    package_path = destination / PACKAGE
    shutil.copyfile(package, package_path)
    wheel_path = destination / wheel['name']
    wheel_cache = ROOT / '.tmp/cache/release-baselines' / wheel['sha256']
    if wheel_cache.is_file() and wheel_cache.stat().st_size == wheel['size'] and digest(wheel_cache) == wheel['sha256']:
        shutil.copyfile(wheel_cache,wheel_path)
    if not wheel_path.is_file() or digest(wheel_path) != wheel['sha256']:
        # Curl supplies bounded socket/elapsed/no-progress handling; the hash is authoritative.
        part = destination / (wheel['name'] + '.part')
        download(['curl', '--fail', '--location', '--silent', '--show-error', '--connect-timeout', '30',
                  '--max-time', '600', '--speed-time', '60', '--speed-limit', '1', wheel['url']], part)
        if part.stat().st_size != wheel['size'] or digest(part) != wheel['sha256']:
            raise ValueError('Baseline wheel differs from its pinned identity')
        os.replace(part, wheel_path)
    wheel_cache.parent.mkdir(parents=True,exist_ok=True)
    if not wheel_cache.exists() or digest(wheel_cache) != wheel['sha256']:
        shutil.copyfile(wheel_path,wheel_cache)
    result = {'package': str(package_path), 'wheel': str(wheel_path), 'version': value}
    if 'constraints' in baseline:
        constraints = destination/'constraints.txt'
        constraints.write_text(baseline['constraints'], encoding='utf-8', newline='\n')
        if digest(constraints) != baseline['constraintsSha256']:
            raise ValueError('Historical dependency constraints changed')
        result['constraints'] = str(constraints)
    return result


def start_state(value, commit, resume=None):
    version(value)
    if not re.fullmatch('[0-9a-f]{40}', commit):
        raise ValueError('Expected full product commit')
    if resume:
        path = Path(resume).resolve()
        state = json.loads(path.read_text(encoding='utf-8'))
        if state['schemaVersion'] != 1 or state['version'] != value or state['sourceCommit'] != commit:
            raise ValueError('Recovery state does not match version/source commit')
        return path
    report = Workspace(ROOT).create('release-state')
    (report / '.keep').touch()
    path = report / 'release-state.json'
    save(path, {'schemaVersion': 1, 'version': value, 'sourceCommit': commit,
                'created': time.time(), 'stages': {}})
    return path


@contextmanager
def state_lock(path):
    lock = Path(path).with_suffix('.lock')
    if lock.exists() and not alive(int(lock.read_text())):
        lock.unlink()
    try:
        with lock.open('x') as stream:
            stream.write(str(os.getpid()))
    except FileExistsError:
        raise ValueError('Another release coordinator is using this recovery record') from None
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def record_stage(path, stage, fields):
    with state_lock(path):
        state=json.loads(path.read_text(encoding='utf-8'))
        state['stages'][stage] = dict(fields, recordedAt=time.time())
        if stage == 'complete':
            state['status'] = 'complete'
        save(path,state)


def wait_run(repo, run_id, timeout=5400):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        run = api(f'repos/{repo}/actions/runs/{run_id}')
        status = (run['status'], run.get('conclusion'))
        if status != last:
            print(f'run {run_id}: {status}', file=sys.stderr, flush=True)
            last = status
        if run['status'] == 'completed':
            return run
        time.sleep(15)
    raise TimeoutError(f'Observation timed out; remote run {run_id} may still be running. Resume the same state.')


def workflow(path, stage, name, fields, retry_failed=False):
    with state_lock(path):
        requested_fields = fields
        state = json.loads(path.read_text(encoding='utf-8'))
        record = state['stages'].get(stage)
        if record and (record.get('requestedFields', record['fields']) != fields or record['workflow'] != name):
            raise ValueError('Workflow inputs changed; refusing to reuse unrelated validation')
        if record and record.get('runId'):
            run = wait_run(REPO, record['runId'])
            record.update(conclusion=run['conclusion'], attempt=run['run_attempt'])
            save(path, state)
            if run['conclusion'] == 'success':
                return record['runId']
            if not retry_failed or stage not in ('build', 'pypi'):
                raise ValueError(f'Workflow failed: {record["runId"]}; inspect the failed stage before retrying')
            if stage == 'build':
                jobs = api(f'repos/{REPO}/actions/runs/{record["runId"]}/jobs?filter=latest&per_page=100')
                if not any(s['name'] in ('Build artifacts and verify Windows startup', 'Restore packages from a completed artifact build')
                           and s['conclusion'] == 'success' for j in jobs['jobs'] for s in j.get('steps', [])):
                    raise ValueError('Failed run has no proven artifact build; refusing an implicit rebuild')
                fields = dict(fields, reuse_build_run_id=record['fields'].get('reuse_build_run_id') or str(record['runId']))
                passed = {s['name'] for j in jobs['jobs'] if j.get('name') == 'build'
                          for s in j.get('steps', []) if s['conclusion'] == 'success'}
                if not fields.get('replacement_commit') and 'Upload validation evidence' in passed:
                    if 'Record passed standard validation' in passed:
                        fields['validation_run_id'] = str(record['runId'])
                    elif 'Bind recovered validation to exact packages and plan' in passed and record['fields'].get('validation_run_id'):
                        fields['validation_run_id'] = record['fields']['validation_run_id']
            elif not fields.get('build_run_id'):
                raise ValueError('PyPI recovery requires the same verified artifact build')
            state.setdefault('previousAttempts', []).append(record)
            record = None
        if not record:
            record = {'workflow': name, 'fields': fields, 'requestedFields': requested_fields, 'dispatchId': uuid.uuid4().hex}
            state['stages'][stage] = record
            save(path, state)
        token = record['dispatchId']
        if not record.get('submitted'):
            record['submitted'] = True
            save(path, state)  # Persist before dispatch: an unknown outcome must never dispatch twice.
            command(['gh', 'workflow', 'run', name, '--repo', REPO, '--ref', 'main',
                     '-f', f'dispatch_id={token}', *[item for k, v in fields.items() for item in ('-f', f'{k}={v}')]])
        if not record.get('runId'):
            for _ in range(40):
                runs = api(f'repos/{REPO}/actions/workflows/{name}/runs?event=workflow_dispatch&per_page=100')['workflow_runs']
                matches = [r for r in runs if r['display_title'].endswith(f' [{token}]')]
                if len(matches) > 1:
                    raise ValueError('Ambiguous workflow dispatch identity')
                if matches:
                    record.update(runId=matches[0]['id'], workflowCommit=matches[0]['head_sha'], attempt=matches[0]['run_attempt'])
                    save(path, state)
                    break
                time.sleep(2)
            else:
                raise TimeoutError('Dispatch outcome unknown; resume this state to rediscover the same dispatch ID')
        run = wait_run(REPO, record['runId'])
        record.update(conclusion=run['conclusion'], attempt=run['run_attempt'])
        save(path, state)
        if run['conclusion'] != 'success':
            raise ValueError(f'Workflow failed: {record["runId"]}; resume after diagnosing its failed stage')
        return record['runId']


def safe_publish(repo, tag, commit, notes, files):
    """Preflight every existing asset before uploading any missing one."""
    try:
        release = api(f'repos/{repo}/releases/tags/{tag}')
    except RuntimeError as error:
        if 'HTTP 404' not in str(error):
            raise
        try:
            existing_tag = tag_commit(repo, tag)
        except RuntimeError as error:
            if 'HTTP 404' not in str(error):
                raise
            target = ['--target', commit]
        else:
            if existing_tag != commit:
                raise ValueError(f'Release tag conflicts with expected source: {tag}')
            # The existing tag pins the source. An unnecessary historical target
            # can require workflow permissions that GITHUB_TOKEN cannot obtain.
            target = ['--verify-tag']
        command(['gh', 'release', 'create', tag, '--repo', repo, *target,
                 '--title', tag, '--notes-file', str(notes), '--latest', *map(str, files)], timeout=300)
        return
    existing = {a['name']: a for a in release['assets']}
    missing = []
    for file in files:
        file = Path(file)
        asset = existing.get(file.name)
        if not asset:
            missing.append(file)
            continue
        expected = digest(file)
        if asset['size'] != file.stat().st_size:
            raise ValueError(f'Release asset conflicts: {file.name}; explicit replacement is required')
        if asset.get('digest') == 'sha256:' + expected:
            continue
        if digest(cached_asset(repo, asset)) != expected:
            raise ValueError(f'Release asset conflicts: {file.name}; explicit replacement is required')
    if missing:
        command(['gh', 'release', 'upload', tag, '--repo', repo, *map(str, missing)], timeout=300)


def plan_digest(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def product_paths(value):
    version(value)
    product = 'zotero-pdf2zh-pro'
    return [f'plugin/build/{product}.xpi', 'plugin/build/update.json',
            f'dist/{PACKAGE}', f'dist/{product}-windows-x64-setup.exe', 'dist/windows-update.json',
            f'dist/{product}-{value}-source.zip',
            f'server/dist/zotero_pdf2zh_pro-{value}-py3-none-any.whl',
            f'server/dist/zotero_pdf2zh_pro-{value}.tar.gz']


def product_identity(value):
    return {Path(name).name: {'size': (ROOT/name).stat().st_size, 'sha256': digest(ROOT/name)}
            for name in product_paths(value)}


def verify_standard_receipt(receipt, plan, artifacts):
    """A passed step/commit alone cannot identify the bytes or coverage it validated."""
    if (receipt.get('schemaVersion') != 1 or receipt.get('status') != 'passed'
            or receipt.get('plan') != plan or receipt.get('planSha256') != plan_digest(plan)
            or receipt.get('lifecycle') != plan['lifecycle']
            or not re.fullmatch(r'[1-9][0-9]*', str(receipt.get('runId', '')))
            or not isinstance(receipt.get('runAttempt'), int) or receipt['runAttempt'] < 1):
        raise ValueError('Standard validation plan or run identity mismatch; rerun selected checks')
    expected_names = {Path(name).name for name in product_paths(plan['version'])}
    if set(artifacts) != expected_names or receipt.get('artifacts') != artifacts:
        raise ValueError('Standard validation used different artifacts')
    for item in artifacts.values():
        if item['size'] <= 0 or not re.fullmatch('[0-9a-f]{64}', item['sha256']):
            raise ValueError('Invalid validated artifact identity')


def record_standard_validation(plan_path, output):
    plan = json.loads(Path(plan_path).read_text(encoding='utf-8-sig'))
    receipt = {'schemaVersion': 1, 'status': 'passed', 'plan': plan, 'planSha256': plan_digest(plan),
               'lifecycle': plan['lifecycle'], 'runId': os.environ['GITHUB_RUN_ID'],
               'runAttempt': int(os.environ['GITHUB_RUN_ATTEMPT']), 'artifacts': product_identity(plan['version'])}
    verify_standard_receipt(receipt, plan, receipt['artifacts'])
    save(Path(output), receipt)


def recover_standard_validation(plan_path, receipt_path, output):
    plan = json.loads(Path(plan_path).read_text(encoding='utf-8-sig'))
    receipt = json.loads(Path(receipt_path).read_text(encoding='utf-8-sig'))
    verify_standard_receipt(receipt, plan, product_identity(plan['version']))
    save(Path(output), receipt)


def attach_plan(plan_path, manifest_path, deferred=False, standard_receipt=None):
    plan = json.loads(Path(plan_path).read_text(encoding='utf-8'))
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if plan['version'] != manifest['version'] or plan['sourceCommit'] != manifest['commit']:
        raise ValueError('Validation plan belongs to different artifacts')
    if not standard_receipt:
        raise ValueError('A passed standard validation receipt is required')
    receipt = json.loads(Path(standard_receipt).read_text(encoding='utf-8-sig'))
    verify_standard_receipt(receipt, plan, manifest['artifacts'])
    manifest.update(validationPlanSha256=plan_digest(plan), deferredExtendedValidation=deferred,
                    standardValidationSha256=plan_digest(receipt))
    save(manifest_path, manifest)
    save(manifest_path.parent/'validation-plan.json', plan)
    save(manifest_path.parent/'standard-validation.json', receipt)


def verify_publication_contract(source, local_receipt=None):
    source = Path(source)
    plan = json.loads((source/'validation-plan.json').read_text(encoding='utf-8'))
    manifest = json.loads((source/'checksums.json').read_text(encoding='utf-8'))
    if (manifest.get('validationPlanSha256') != plan_digest(plan) or manifest['commit'] != plan['sourceCommit']
            or manifest['version'] != plan['version']):
        raise ValueError('Missing/mismatched validation contract; revalidate existing artifacts')
    validate_plan(plan)
    receipt_path = source/'standard-validation.json'
    if not receipt_path.is_file():
        raise ValueError('Missing standard validation receipt; revalidate existing artifacts')
    standard = json.loads(receipt_path.read_text(encoding='utf-8-sig'))
    if manifest.get('standardValidationSha256') != plan_digest(standard):
        raise ValueError('Standard validation receipt digest mismatch')
    verify_standard_receipt(standard, plan, manifest['artifacts'])
    if manifest.get('deferredExtendedValidation') and len(plan['cases']) > 1:
        receipt = local_receipt or {}
        expected = {(case['id'], case['baseline']) for case in plan['cases'] if case['id'] != 'recent-upgrade'}
        actual = {(case['id'], case['baseline']) for case in receipt.get('cases', []) if case['status'] == 'passed'}
        waived = set()
        for case in receipt.get('waivedCases', []):
            identity = (case.get('id'), case.get('baseline'))
            if (identity != ('legacy-upgrade', policy()['legacyBaseline']) or identity not in expected
                    or not isinstance(case.get('reason'), str) or not case['reason'].strip()):
                raise ValueError('Only an explicitly requested legacy upgrade waiver is permitted')
            waived.add(identity)
        if (receipt.get('status') != 'passed' or receipt.get('commit') != plan['sourceCommit']
                or receipt.get('planSha256') != plan_digest(plan)
                or receipt.get('harnessSha256') != plan.get('harnessSha256')
                or not expected.issubset(actual | waived)):
            raise ValueError('Required local extended validation is incomplete')
        for key, name in [('packageSha256', PACKAGE), ('wheelSha256', f'zotero_pdf2zh_pro-{plan["version"]}-py3-none-any.whl')]:
            if receipt.get(key) != manifest['artifacts'][name]['sha256']:
                raise ValueError('Local validation used different artifacts')
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    state = sub.add_parser('state')
    state.add_argument('version'); state.add_argument('commit'); state.add_argument('--resume', type=Path)
    flow = sub.add_parser('workflow')
    flow.add_argument('state', type=Path); flow.add_argument('stage'); flow.add_argument('workflow')
    flow.add_argument('--field', action='append', default=[]); flow.add_argument('--retry-failed', action='store_true')
    record = sub.add_parser('record')
    record.add_argument('state',type=Path); record.add_argument('stage')
    record.add_argument('--field',action='append',default=[])
    watch = sub.add_parser('watch')
    watch.add_argument('run'); watch.add_argument('--repo', default=REPO); watch.add_argument('--timeout', type=int, default=5400)
    fetch = sub.add_parser('download')
    fetch.add_argument('run'); fetch.add_argument('name'); fetch.add_argument('directory', type=Path)
    plan = sub.add_parser('plan')
    plan.add_argument('version'); plan.add_argument('commit'); plan.add_argument('output', type=Path)
    plan.add_argument('--full', action='store_true'); plan.add_argument('--compatibility', action='store_true')
    baseline = sub.add_parser('baseline')
    baseline.add_argument('plan', type=Path); baseline.add_argument('version'); baseline.add_argument('directory', type=Path)
    attach = sub.add_parser('attach-plan')
    attach.add_argument('plan', type=Path); attach.add_argument('manifest', type=Path); attach.add_argument('--deferred', action='store_true')
    attach.add_argument('--standard-receipt', type=Path, required=True)
    validation = sub.add_parser('record-validation')
    validation.add_argument('plan', type=Path); validation.add_argument('output', type=Path)
    recovery = sub.add_parser('recover-validation')
    recovery.add_argument('plan', type=Path); recovery.add_argument('receipt', type=Path); recovery.add_argument('output', type=Path)
    plan_hash = sub.add_parser('plan-digest'); plan_hash.add_argument('plan', type=Path)
    sub.add_parser('harness-digest')
    legacy_tool=sub.add_parser('legacy-uv'); legacy_tool.add_argument('destination',type=Path)
    candidate = sub.add_parser('published-candidate')
    candidate.add_argument('plan', type=Path)
    contract = sub.add_parser('verify-contract')
    contract.add_argument('directory', type=Path); contract.add_argument('--receipt', type=Path)
    publish = sub.add_parser('publish')
    publish.add_argument('tag'); publish.add_argument('commit'); publish.add_argument('notes', type=Path)
    publish.add_argument('files', nargs='+', type=Path); publish.add_argument('--repo', default=REPO)
    args = parser.parse_args()
    if args.action == 'state':
        print(start_state(args.version, args.commit, args.resume).as_posix())
    elif args.action == 'workflow':
        print(workflow(args.state, args.stage, args.workflow, dict(item.split('=', 1) for item in args.field), args.retry_failed))
    elif args.action == 'record':
        record_stage(args.state,args.stage,dict(item.split('=',1) for item in args.field))
    elif args.action == 'watch':
        if wait_run(args.repo, args.run, args.timeout)['conclusion'] != 'success':
            raise ValueError(f'Run {args.run} failed')
    elif args.action == 'download':
        args.directory.mkdir(parents=True, exist_ok=True)
        # Download the named archive as a bounded byte stream, then extract locally.
        artifacts = api(f'repos/{REPO}/actions/runs/{args.run}/artifacts?per_page=100')['artifacts']
        matches = [a for a in artifacts if a['name'] == args.name and not a['expired']]
        if len(matches) != 1:
            raise ValueError('Missing/ambiguous unexpired artifact')
        with Workspace(ROOT).run('artifact-download') as (work, _):
            archive = work / 'artifact.zip'
            download(['gh', 'api', f'repos/{REPO}/actions/artifacts/{matches[0]["id"]}/zip'], archive)
            import zipfile
            with zipfile.ZipFile(archive) as zipped:
                for name in zipped.namelist():
                    if not (args.directory / name).resolve().is_relative_to(args.directory.resolve()):
                        raise ValueError('Artifact archive escapes destination')
                zipped.extractall(args.directory)
    elif args.action == 'plan':
        print(json.dumps(make_plan(args.version, args.commit, args.output, args.full, args.compatibility)))
    elif args.action == 'baseline':
        print(json.dumps(fetch_baseline(json.loads(args.plan.read_text(encoding='utf-8')), args.version, args.directory)))
    elif args.action == 'attach-plan':
        attach_plan(args.plan, args.manifest, args.deferred, args.standard_receipt)
    elif args.action == 'record-validation':
        record_standard_validation(args.plan, args.output)
    elif args.action == 'recover-validation':
        recover_standard_validation(args.plan, args.receipt, args.output)
    elif args.action == 'plan-digest':
        print(plan_digest(json.loads(args.plan.read_text(encoding='utf-8'))))
    elif args.action == 'harness-digest':
        print(harness_digest(ROOT))
    elif args.action == 'legacy-uv':
        legacy_uv(args.destination)
    elif args.action == 'verify-contract':
        verify_publication_contract(args.directory, json.loads(args.receipt.read_text(encoding='utf-8-sig')) if args.receipt else None)
    elif args.action == 'published-candidate':
        pages = json.loads(command(['gh','api','--paginate','--slurp',f'repos/{REPO}/releases?per_page=100']))
        releases = [r for page in pages for r in page if not r['draft'] and not r['prerelease']
                    and re.fullmatch(r'v\d+\.\d+\.\d+', r['tag_name'])]
        latest = max(releases, key=lambda r: version(r['tag_name']))
        value = latest['tag_name'].removeprefix('v')
        baseline = freeze_baseline(REPO,value,latest)
        make_plan(value,baseline['commit'],args.plan,compatibility=True)
        with Workspace(ROOT).run('compatibility-candidate') as (work,_):
            candidate_files = fetch_baseline({'baselines':{value:baseline}},value,work)
            for source, destination in [(candidate_files['package'],ROOT/'dist'/PACKAGE),
                                         (candidate_files['wheel'],ROOT/'server/dist'/baseline['wheel']['name'])]:
                destination.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(source,destination)
        print(json.dumps({'version':value,'commit':baseline['commit']}))
    else:
        safe_publish(args.repo, args.tag, args.commit, args.notes, args.files)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as error:
        print(f'release: {error}', file=sys.stderr)
        raise SystemExit(1)
