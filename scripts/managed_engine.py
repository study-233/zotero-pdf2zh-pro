"""Install a frozen engine and hand off a private loopback listener only when idle."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import errno
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import build_opener, HTTPRedirectHandler, ProxyHandler, Request
import zipfile

class LoopbackOnly(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


LOCAL = build_opener(ProxyHandler({}), LoopbackOnly())


@contextmanager
def locked(path):
    # The OS releases the lock even if Zotero or an installer crashes.
    with path.open('a+b') as f:
        if os.name == 'nt':
            import msvcrt
            if not f.tell():
                f.write(b'0'); f.flush()
            f.seek(0)
            while True:
                try:
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise
                    time.sleep(.2)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX)
        yield


def write(path, value):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, mode='w', encoding='utf-8') as f:
        f.write(value)
    Path(f.name).replace(path)


def request(port, endpoint, token=None, minimum_idle=0):
    headers = {'X-PDF2ZH-Control-Token': token} if token else {}
    if minimum_idle:
        headers['X-PDF2ZH-Minimum-Idle'] = str(minimum_idle)
    req = Request(f'http://127.0.0.1:{port}{endpoint}', headers=headers,
                  method='POST' if token else 'GET')
    with LOCAL.open(req, timeout=4) as response:
        return json.load(response)


def health(port):
    try:
        result = request(port, '/health')
    except URLError as exc:
        if isinstance(exc.reason, ConnectionRefusedError):
            return None
        raise
    if result.get('status') not in ('ok', 'degraded') or not result.get('version'):
        raise RuntimeError('Unexpected service on the configured port')
    return result


def revision(value):
    if not re.fullmatch('[a-f0-9]{64}', value):
        raise ValueError('Invalid engine revision')
    return value


def interpreter(folder):
    return folder / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def version_folder(root, target):
    target = revision(target)
    legacy = root / 'versions' / target
    # A ready legacy environment keeps its absolute venv paths. New Windows
    # installs use a compact directory; .ready still records the full revision.
    folder = legacy
    if os.name == 'nt' and not (legacy / '.ready').is_file():
        folder = root / 'versions' / target[:16]
    ready = folder / '.ready'
    if ready.is_file() and ready.read_text().strip() != target:
        raise ValueError('Engine revision directory collision')
    return folder


def prepare(root, bundle, target, uv):
    destination = version_folder(root, target)
    if (destination / '.ready').is_file() and interpreter(destination).is_file():
        return
    manifest = json.loads((bundle / 'manifest.json').read_bytes())
    archive = bundle / 'service.zip'
    if manifest['id'] != target or hashlib.sha256(archive.read_bytes()).hexdigest() != manifest['sha256']:
        raise ValueError('Engine payload checksum mismatch')
    destination.mkdir(parents=True, exist_ok=True)
    # Venv paths are absolute; create it at the final location and publish only
    # the .ready marker after testing. A retry can reuse downloaded wheels.
    with zipfile.ZipFile(archive) as z:
        z.extractall(destination)
    shutil.copyfile(bundle / 'manager.py', destination / 'manager.py')
    env = {**os.environ, 'UV_PROJECT_ENVIRONMENT': str(destination / '.venv'),
           'UV_CACHE_DIR': str(root / 'cache'), 'PYTHONNOUSERSITE': '1'}
    reinstall = ['--reinstall'] if (destination / '.repair').is_file() else []
    subprocess.run([str(uv), 'sync', *reinstall, '--no-config', '--frozen', '--no-dev', '--no-install-project',
                    '--no-build', '--python', sys.executable, '--directory', str(destination)],
                   env=env, check=True)
    with tempfile.TemporaryDirectory(dir=root) as data:
        subprocess.run([str(interpreter(destination)), str(destination / 'manager.py'), 'probe'],
                       cwd=destination, env={**env, 'PDF2ZH_DATA_DIR': data, 'PDF2ZH_TRANSLATION_CACHE_DIR': data,
                                             'PDF2ZH_CONFIG_DIR': str(Path(data) / 'config'),
                                             'PDF2ZH_BABELDOC_CACHE_DIR': str(Path(data) / 'babeldoc')}, check=True, timeout=600)
    write(destination / '.ready', target)
    (destination / '.repair').unlink(missing_ok=True)


def launch(root, target, port, owner):
    folder = version_folder(root, target)
    # Gecko terminates its Windows job when the installer exits. The service
    # must leave that job to keep serving after nativeCommand has returned.
    options = {'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
               | subprocess.CREATE_BREAKAWAY_FROM_JOB} if os.name == 'nt' else {'start_new_session': True}
    with (root / 'startup.log').open('wb') as log:
        process = subprocess.Popen([str(interpreter(folder)), str(folder / 'manager.py'),
                                    'serve', str(root), target, str(port), owner],
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                   cwd=folder, close_fds=True, **options)
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        if process.poll() is not None:
            # Another profile or an existing external installation won the port.
            other = health(port)
            if other and other.get('managedOwner') != owner:
                return 'external'
            raise RuntimeError('Engine exited before becoming ready')
        try:
            live = health(port)
        except (TimeoutError, URLError, ConnectionError):
            live = None
        if live and live.get('managedOwner') == owner and live.get('managedRevision') == target:
            return 'ready'
        time.sleep(.2)
    # Never kill by PID or give a second engine access to the same data.
    raise RuntimeError('Engine did not become ready in time')


def ensure(root, bundle, uv, port, keep_stopped=False):
    target = revision(json.loads((bundle / 'manifest.json').read_bytes())['id'])
    with locked(root / 'install.lock'):
        active_file = root / 'active'
        backup_file = root / 'previous'
        backup = revision(backup_file.read_text().strip()) if backup_file.exists() else None
        previous = revision(active_file.read_text().strip()) if active_file.exists() else None
        owner_file = root / 'owner'
        if not owner_file.exists():
            write(owner_file, secrets.token_hex(24))
        owner = owner_file.read_text().strip()
        live = health(port)
        if live:
            if live.get('managedOwner') != owner:
                return 'external'
            previous = revision(live['managedRevision'])
            if previous == target:
                write(active_file, target)
                return 'ready'
            if live['status'] != 'ok' or live.get('maintenance', {}).get('busy') or live.get('maintenance', {}).get('idleSeconds', 60) < 60:
                return 'deferred'
        try:
            prepare(root, bundle, target, uv)
        except Exception:
            if live:
                return 'fallback'
            if keep_stopped:
                raise
            recovery = previous if previous != target else backup
            if recovery:
                return 'fallback' if launch(root, recovery, port, owner) == 'ready' else 'external'
            raise
        if keep_stopped and live is None:
            if health(port):
                return 'deferred'
            if previous and previous != target:
                write(backup_file, previous)
            write(active_file, target)
            return 'ready'
        if live:
            current = health(port)
            if not current or current.get('managedOwner') != owner or current.get('managedRevision') != previous:
                return 'deferred'
            maintenance = current.get('maintenance', {})
            if current['status'] != 'ok' or maintenance.get('busy') or maintenance.get('idleSeconds', 60) < 60:
                return 'deferred'
            token = (root / 'data' / '.control-token').read_text().strip()
            try:
                request(port, '/internal/managed-replace', token, minimum_idle=60)
            except HTTPError as exc:
                if exc.code == 409:
                    return 'deferred'
                raise
            # Wait for the retiring process to release its data lock, including
            # TaskManager.close(), before the replacement opens tasks.json.
            with locked(root / 'serving.lock'):
                pass
        try:
            state = launch(root, target, port, owner)
        except Exception:
            if health(port) is not None:
                raise
            failed = version_folder(root, target)
            if failed.is_dir():
                (failed / '.ready').unlink(missing_ok=True)
                write(failed / '.repair', target)
            recovery = previous if previous != target else backup
            if recovery:
                if launch(root, recovery, port, owner) != 'ready':
                    return 'external'
                return 'fallback'
            if previous != target:
                raise
            # A sole installed runtime can also be repaired after local file loss.
            prepare(root, bundle, target, uv)
            state = launch(root, target, port, owner)
        if state == 'ready':
            if previous and previous != target:
                backup = previous
                write(backup_file, backup)
            write(active_file, target)
            # Keep the preceding working engine as well as the new one.
            retained = {version_folder(root, value).name for value in (target, backup) if value}
            for folder in (root / 'versions').iterdir():
                if re.fullmatch('[a-f0-9]{16}|[a-f0-9]{64}', folder.name) and folder.name not in retained:
                    shutil.rmtree(folder, ignore_errors=True)
        return state


def relocate(source, destination, bundle, uv, port, binding):
    """Rebuild at the final absolute path, then publish one profile location record."""
    source, destination = source.resolve(strict=True), destination.resolve(strict=True)
    if source.is_relative_to(destination) or destination.is_relative_to(source):
        raise ValueError('Overlapping engine directories')
    if binding.name != 'pdf2zh-engine-location.json' or binding.is_symlink():
        raise ValueError('Invalid profile location record')
    target = revision(json.loads((bundle / 'manifest.json').read_bytes())['id'])
    with locked(source / 'install.lock'), locked(destination / 'install.lock'):
        old_binding = binding.read_text('utf-8') if binding.exists() else None
        owner = (source / 'owner').read_text().strip()
        if old_binding is None:
            if source != (binding.parent / 'pdf2zh-engine').resolve():
                raise ValueError('Unexpected default engine')
        else:
            saved = json.loads(old_binding)
            if Path(saved['root']).resolve() != source or saved['owner'] != owner:
                raise ValueError('Engine location changed during relocation')
        old_target = revision((source / 'active').read_text().strip())
        marker = destination / '.pdf2zh-relocation.json'
        reserved = False
        if marker.is_file():
            reservation = json.loads(marker.read_text('utf-8'))
            reserved = (Path(reservation['source']).resolve() == source and reservation['owner'] == owner
                        and Path(reservation['binding']).resolve() == binding.resolve())
            if not reserved:
                raise ValueError('Destination reserved by another installation')
        if (destination / 'active').exists() or (destination / 'data').exists():
            if not reserved:
                raise ValueError('Destination already in use')
            # The source still owns the binding. Keep unpublished data from an interrupted attempt.
            recovery = Path(tempfile.mkdtemp(prefix='recovery-', dir=destination))
            for name in ('active', 'owner', 'data'):
                item = destination / name
                if item.exists():
                    item.rename(recovery / name)
        if target != old_target:
            raise ValueError('Update the engine before relocation')
        live = health(port)
        if live and live.get('managedOwner') != owner:
            return 'external'
        if live and (live['status'] != 'ok' or live.get('maintenance', {}).get('busy')):
            return 'deferred'
        prepare(destination, bundle, target, uv)
        folder = version_folder(destination, target)
        # Reuse the same hashing and task-path rebasing rules as the installed service.
        import importlib.util
        spec = importlib.util.spec_from_file_location('engine_storage', folder / 'runtime_storage.py')
        storage = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(storage)
        live = health(port)
        if live and live.get('managedOwner') != owner:
            return 'external'
        if live:
            token = (source / 'data/.control-token').read_text().strip()
            try:
                request(port, '/internal/managed-replace', token)
            except HTTPError as error:
                if error.code == 409:
                    return 'deferred'
                raise
        published = False
        try:
            with locked(source / 'serving.lock'):
                old_data, data = source / 'data', destination / 'data'
                expected = storage._manifest(old_data)
                required = sum((old_data / name).stat().st_size for name in expected)
                if shutil.disk_usage(destination).free < required:
                    raise OSError('Insufficient space for engine data')
                with tempfile.TemporaryDirectory(dir=destination, prefix='migration-') as staging:
                    copied = Path(staging) / 'data'
                    shutil.copytree(old_data, copied, symlinks=True)
                    if storage._manifest(copied) != expected:
                        raise OSError('Engine data copy verification failed')
                    storage._rebase_tasks(copied, old_data, data)
                    copied.rename(data)
                new_owner = secrets.token_hex(24)
                write(destination / 'owner', new_owner)
                write(destination / 'active', target)
                write(binding, json.dumps({'root':str(destination), 'owner':new_owner}))
                published = True
            if live and launch(destination, target, port, new_owner) != 'ready':
                raise RuntimeError('Migrated engine failed its health check')
        except Exception:
            if published:
                if old_binding is None:
                    binding.unlink()
                else:
                    write(binding, old_binding)
            if live and health(port) is None:
                launch(source, old_target, port, owner)
            raise
        marker.unlink(missing_ok=True)
        return 'ready'


def restart(root, port):
    """Boot an installed engine without requiring the network or bootstrap tool."""
    with locked(root / 'install.lock'):
        owner = (root / 'owner').read_text().strip()
        live = health(port)
        if live:
            return 'ready' if live.get('managedOwner') == owner else 'external'
        active = revision((root / 'active').read_text().strip())
        try:
            return launch(root, active, port, owner)
        except Exception:
            backup = root / 'previous'
            if not backup.exists() or health(port) is not None:
                raise
            state = launch(root, revision(backup.read_text().strip()), port, owner)
            return 'fallback' if state == 'ready' else state


def serve(root, target, port, owner):
    from werkzeug.serving import make_server
    from werkzeug.wrappers import Request as LocalRequest, Response
    data = root / 'data'
    os.environ['PDF2ZH_DATA_DIR'] = str(data)
    os.environ['PDF2ZH_CONFIG_DIR'] = str(data / 'config')
    os.environ['PDF2ZH_CHATGPT_AUTH_DIR'] = str(data / 'chatgpt-auth')
    os.environ['PDF2ZH_TRANSLATION_CACHE_DIR'] = str(data / 'cache')
    os.environ['PDF2ZH_BABELDOC_CACHE_DIR'] = str(data / 'cache' / 'babeldoc')
    with locked(root / 'serving.lock'):
        # Reserve the port before importing server: importing TaskManager opens
        # persistent tasks and performs crash recovery.
        if os.name == 'nt':
            import socket
            from werkzeug.serving import ThreadedWSGIServer

            class ExclusiveServer(ThreadedWSGIServer):
                allow_reuse_address = False

                def server_bind(self):
                    self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                    super().server_bind()

            httpd = ExclusiveServer('127.0.0.1', port, lambda *_: [])
        else:
            httpd = make_server('127.0.0.1', port, lambda *_: [], threaded=True)
        import server
        from maintenance import control_token
        server.app.extensions['managed_engine'] = True
        token = control_token(data)

        @server.app.after_request
        def identify(response):
            from flask import request as flask_request
            if flask_request.path == '/health' and response.is_json:
                response.set_data(json.dumps({**response.get_json(), 'managedOwner': owner,
                                              'managedRevision': target}))
            return response

        def dispatch(environ, start):
            if environ.get('PATH_INFO') != '/internal/managed-replace':
                return server.app(environ, start)
            req = LocalRequest(environ)
            if (req.method != 'POST' or req.remote_addr != '127.0.0.1' or req.headers.get('Origin')
                    or not hmac.compare_digest(req.headers.get('X-PDF2ZH-Control-Token', ''), token)):
                code = 403
            elif not server.app.extensions['update_maintenance'].acquire(
                    60 if req.headers.get('X-PDF2ZH-Minimum-Idle') == '60' else 0):
                code = 409
            else:
                code = 200
            response = Response('{}', status=code, mimetype='application/json')
            if code == 200:
                # Werkzeug closes the WSGI iterator after sending the response.
                # Stopping inside the handler can drop the acknowledgement.
                response.call_on_close(lambda: threading.Thread(target=httpd.shutdown, daemon=True).start())
            return response(environ, start)

        httpd.app = dispatch
        sys.argv = [sys.argv[0], '--data-dir', str(data), '--port', str(port),
                    '--log-file', str(root / 'service.log')]
        try:
            def serve_checked():
                # Validate persistent state before admitting work on a replacement.
                if server.app.test_client().get('/health').get_json()['status'] != 'ok':
                    raise RuntimeError('Engine data health check failed')
                import logging
                for handler in list(logging.getLogger().handlers):
                    if type(handler) is logging.StreamHandler:
                        logging.getLogger().removeHandler(handler)
                httpd.serve_forever()

            server.main(serve_http=serve_checked)
        finally:
            httpd.server_close()


if __name__ == '__main__':
    if sys.argv[1] == 'probe':
        import server
        try:
            if server.app.test_client().get('/health').get_json()['status'] != 'ok':
                raise RuntimeError('Engine self-test failed')
        finally:
            server.TASK_MANAGER.close()
            server.GLOSSARY_MANAGER.close()
            server.TEXT_TRANSLATOR.close()
    elif sys.argv[1] == 'restart':
        print(restart(Path(sys.argv[2]), int(sys.argv[3])), flush=True)
    elif sys.argv[1] == 'serve':
        serve(Path(sys.argv[2]), revision(sys.argv[3]), int(sys.argv[4]), sys.argv[5])
    else:
        parser = argparse.ArgumentParser()
        parser.add_argument('root', type=Path)
        parser.add_argument('bundle', type=Path)
        parser.add_argument('uv', type=Path)
        parser.add_argument('--port', type=int, default=8890)
        parser.add_argument('--keep-stopped', action='store_true')
        parser.add_argument('--relocate-from', type=Path)
        parser.add_argument('--binding', type=Path)
        args = parser.parse_args()
        if args.relocate_from:
            if not args.binding:
                parser.error('--binding is required for relocation')
            print(relocate(args.relocate_from, args.root, args.bundle, args.uv, args.port, args.binding), flush=True)
        else:
            print(ensure(args.root, args.bundle, args.uv, args.port, args.keep_stopped), flush=True)
