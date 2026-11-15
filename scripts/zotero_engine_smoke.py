"""Cold-install the universal XPI engine in a real disposable Zotero profile."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
import zipfile

from managed_engine import health, interpreter, request, locked, version_folder
from workspace import ROOT, Workspace
from zotero_acceptance import ZOTERO, bundle_module, write_profile

HARNESS = r"""
function install() {}
function uninstall() {}
function shutdown() {}
function startup({rootURI}) { void run(rootURI); }
async function run(uri) {
    try {
        await Zotero.uiReadyPromise;
        for (let n = 0; !Zotero.pdf2zhpro?.data.locale && n < 200; n++)
            await Zotero.Promise.delay(100);
        const sandbox = { Zotero, ChromeUtils, Services, Ci, Cc, IOUtils, PathUtils };
        Services.scriptloader.loadSubScript(uri + 'test.js', sandbox);
        const state = await sandbox.smokeModule.runEngineManager(ROOT_PATH, PORT);
        if (state !== 'ready') throw Error(state);
        await IOUtils.writeUTF8(RECEIPT, JSON.stringify({version: Zotero.version, state}));
    } catch (e) {
        await IOUtils.writeUTF8(RECEIPT, JSON.stringify({error: String(e), stack: e.stack}));
    } finally { Services.startup.quit(Ci.nsIAppStartup.eAttemptQuit); }
}
"""


def stop_owned(root, port):
    owner_file = root / 'owner'
    if owner_file.is_file() and (root / 'data/.control-token').exists():
        live = health(port)
        if live and live.get('managedOwner') == owner_file.read_text().strip():
            request(port, '/internal/managed-replace', (root / 'data/.control-token').read_text().strip())
            with locked(root / 'serving.lock'):
                pass


def verify_update(root, port, work):
    # Seed real legacy-format data only while the owned server has stopped.
    stop_owned(root, port)
    old = (root / 'active').read_text().strip()
    folder = version_folder(root, old)
    python = interpreter(folder)
    manager = folder / 'manager.py'
    fixture = ROOT / 'scripts/release-data-fixtures.py'
    data = root / 'data'
    env = {**os.environ, 'PYTHONPATH': str(folder), 'UV_OFFLINE': '1',
           'PDF2ZH_BABELDOC_CACHE_DIR': str(data / 'cache/babeldoc')}
    def run(*args):
        return subprocess.check_output([str(python), *map(str, args)], cwd=folder, env=env,
                                       text=True, encoding='utf-8', timeout=600).strip()
    run(fixture, 'seed', data)
    assert run(manager, 'restart', root, port).endswith('ready')
    assert any(task['taskId'] == 'release-compatibility-task' for task in request(port, '/tasks')['tasks'])
    staged = work / 'update'
    staged.mkdir()
    with zipfile.ZipFile(ROOT / 'plugin/build/zotero-pdf2zh-pro.xpi') as z:
        for name in ('manager.py', 'manifest.json', 'service.zip'):
            (staged / name).write_bytes(z.read('content/engine/' + name))
    spec = json.loads((staged / 'manifest.json').read_bytes())
    # Exercise a distinct engine revision from the same frozen dependency set.
    new = hashlib.sha256((old + '-acceptance').encode()).hexdigest()
    spec['id'] = new
    (staged / 'manifest.json').write_text(json.dumps(spec))
    uv = root / (spec['uv'] + ('-uv.exe' if os.name == 'nt' else '-uv'))
    def install():
        return run(staged / 'manager.py', root, staged, uv, '--port', port).splitlines()[-1]
    # Hold a real in-flight request before its body has finished arriving.
    with socket.create_connection(('127.0.0.1', port)) as upload:
        upload.sendall(b'POST /translate-text HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: 3\r\n\r\n{')
        deadline = time.monotonic() + 5
        while not health(port)['maintenance']['busy']:
            if time.monotonic() > deadline:
                raise RuntimeError('Test request did not hold the maintenance gate')
            time.sleep(.05)
        assert install() == 'deferred'
        assert health(port)['managedRevision'] == old
        upload.sendall(b'} ')
    deadline = time.monotonic() + 10
    while health(port)['maintenance']['busy']:
        if time.monotonic() > deadline:
            raise RuntimeError('Test request did not release the maintenance gate')
        time.sleep(.05)
    assert install() == 'ready' and health(port)['managedRevision'] == new
    assert (root / 'previous').read_text().strip() == old
    assert any(t['taskId'] == 'release-compatibility-task' for t in request(port, '/tasks')['tasks'])
    # An older installed dictionary stays usable even when the catalog advertises an update.
    expected = json.loads((data / 'compatibility-fixture.json').read_bytes())['glossaryPack']['sha256']
    pack = next(p for p in request(port, '/glossaries')['packs'] if p['id'] == 'computing')
    assert any(row['sha256'] == expected for row in pack['installedVersions'])
    # Corrupt an unpublished candidate: the live service and its data must survive.
    spec['id'] = '0' * 64
    (staged / 'manifest.json').write_text(json.dumps(spec))
    (staged / 'service.zip').write_bytes(b'damaged')
    assert install() == 'fallback' and health(port)['managedRevision'] == new
    stop_owned(root, port)
    candidate = version_folder(root, new)
    subprocess.run([interpreter(candidate), fixture, 'verify', data], cwd=candidate,
                   env={**env, 'PYTHONPATH': str(candidate)}, check=True, timeout=120)


def run(work, report):
    profile = work / 'profile'
    library = work / 'library'
    extensions = profile / 'extensions'
    extensions.mkdir(parents=True)
    library.mkdir()
    root = profile / 'pdf2zh-engine'
    write_profile(profile, library, {'extensions.zotero.pdf2zhpro.new_serverip': 'http://127.0.0.1:1'})
    shutil.copyfile(ROOT / 'plugin/build/zotero-pdf2zh-pro.xpi', extensions / 'zotero-pdf2zh-pro@study-233.xpi')
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
    receipt = report / 'engine-smoke.json'
    manifest = dict(manifest_version=2, name='Engine acceptance', version='1',
                    applications={'zotero': {'id': 'engine-smoke@pdf2zh.invalid',
                    'update_url': 'https://example.invalid/updates.json', 'strict_min_version': '8.0',
                    'strict_max_version': '10.0.*'}})
    with zipfile.ZipFile(extensions / 'engine-smoke@pdf2zh.invalid.xpi', 'w') as z:
        z.writestr('manifest.json', json.dumps(manifest))
        z.writestr('test.js', bundle_module('export {runEngineManager} from "./src/modules/managedEngine"'))
        z.writestr('bootstrap.js', HARNESS.replace('ROOT_PATH', json.dumps(str(root))).replace('PORT', str(port))
                   .replace('RECEIPT', json.dumps(str(receipt))))
    binary = os.environ.get('ZOTERO_BIN') or ZOTERO
    try:
        with (report / 'zotero.log').open('w') as f:
            subprocess.run([binary, '-no-remote', '-profile', profile, '--dataDir', library],
                           env={**os.environ, 'MOZ_NO_REMOTE': '1'}, stdout=f, stderr=subprocess.STDOUT,
                           timeout=1200, check=True)
        result = json.loads(receipt.read_text('utf-8'))
        if result.get('state') != 'ready':
            raise RuntimeError(result)
        live = health(port)
        assert live['status'] == 'ok' and live['capabilities']['managedEngine']
        assert live['managedOwner'] == (root / 'owner').read_text().strip()
        verify_update(root, port, work)
        result['checks'] = ['cold install', 'detached process', 'offline restart', 'active request deferred',
                            'idle update', 'damaged update fallback', 'persistent release fixtures']
        receipt.write_text(json.dumps(result, indent=2), 'utf-8')
        print('Verified managed engine in Zotero', result['version'], result['checks'], flush=True)
    finally:
        stop_owned(root, port)


if __name__ == '__main__':
    with Workspace().run('zotero-engine-smoke') as (work, report):
        print(report, flush=True)
        run(work, report)
