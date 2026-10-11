"""Bounded, local Codex discovery. No shell startup files or credentials are read."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import weakref

CACHE_SECONDS = 60
CANDIDATE_SECONDS = 5
DISCOVERY_SECONDS = 30
FALLBACK_CODES = frozenset({
    "codex_not_installed", "codex_invalid_path", "codex_dependency_missing",
    "codex_start_failed", "codex_permission_denied", "codex_arch_mismatch",
    "codex_version_too_old", "codex_version_unrecognized", "codex_incompatible",
    "codex_protocol_error", "codex_process_exited", "codex_timeout",
})


@dataclass(frozen=True)
class Candidate:
    path: str
    source: str
    issue: str | None = None


def file_identity(path):
    try:
        stat = Path(path).stat()
        return (str(Path(path).resolve()), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
    except (OSError, ValueError):
        return (str(path), None)


def environment_identity():
    # Private in-memory cache identity only; never serialized into diagnostics.
    return tuple(sorted(os.environ.items()))


def version_key(version):
    """Semver ordering, retaining numeric prerelease components (alpha.10 > alpha.2)."""
    core, _, pre = version.partition("-")
    release = tuple(int(n) for n in core.split("+")[0].split("."))
    parts = tuple((0, int(p)) if p.isdigit() else (1, p)
                  for p in pre.split("+")[0].split(".")) if pre else ()
    return release, not bool(pre), parts


def _registered_mac_apps(check):
    query = '(kMDItemCFBundleIdentifier == "com.openai.chat*" || kMDItemCFBundleIdentifier == "com.openai.codex*")'
    try:
        with subprocess.Popen(["/usr/bin/mdfind", query], stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, text=True) as process:
            deadline = time.monotonic() + 1
            while True:
                try:
                    if check:
                        check()
                    output, _ = process.communicate(timeout=0.05)
                    return [Path(line) for line in output.splitlines() if line.endswith(".app")]
                except subprocess.TimeoutExpired:
                    if time.monotonic() < deadline:
                        continue
                    process.kill()
                    process.communicate()
                    return []
                except BaseException:
                    process.kill()
                    process.communicate()
                    raise
    except OSError:
        return []


def _npm_native(entry, system, arch):
    """Resolve only official package layouts; never execute a Windows batch shim."""
    resolved = entry.resolve()
    roots = []
    if resolved.name == "codex.js":
        roots.append(resolved.parent.parent)
    if entry.suffix.lower() in (".cmd", ".bat", ".ps1"):
        roots.append(entry.parent / "node_modules/@openai/codex")
    for root in roots:
        try:
            metadata = json.loads((root / "package.json").read_text(encoding="utf-8"))
            if metadata.get("name") != "@openai/codex":
                continue
        except (OSError, ValueError, AttributeError):
            continue
        target = {"darwin": "apple-darwin", "win32": "pc-windows-msvc"}.get(system, "unknown-linux-musl")
        platform_name = {"darwin": "darwin", "win32": "win32"}.get(system, "linux")
        package = f"codex-{platform_name}-{'arm64' if arch == 'aarch64' else 'x64'}"
        vendors = [root / "node_modules/@openai" / package / "vendor",
                   root.parent / package / "vendor", root / "vendor"]
        executable = "codex.exe" if system == "win32" else "codex"
        for vendor in vendors:
            for subdir in ("bin", "codex"):
                native = vendor / f"{arch}-{target}" / subdir / executable
                if native.is_file():
                    return native, None
        return entry, "codex_dependency_missing"
    if system == "win32" and entry.suffix.lower() != ".exe":
        return entry, "codex_dependency_missing"
    return entry, None


def collect_candidates(cli_path=None, *, system=None, check=None):
    system = system or sys.platform
    env = os.environ
    windows = system == "win32"
    executable = "codex.exe" if windows else "codex"
    machine = (env.get("PROCESSOR_ARCHITEW6432") or env.get("PROCESSOR_ARCHITECTURE")
               or platform.machine()).lower() if windows else platform.machine().lower()
    arch = "aarch64" if machine in ("arm64", "aarch64") else "x86_64"
    raw = []
    if cli_path is not None and (not isinstance(cli_path, str) or "\x00" in cli_path):
        from codex_client import CodexError
        raise CodexError("codex_invalid_path", "Codex CLI 路径必须为有效文本。")
    explicit = bool(cli_path and cli_path.strip())
    if explicit:
        value = os.path.expanduser(cli_path.strip())
        raw.append((Path(shutil.which(value) or value), "manual"))
    else:
        if system == "darwin":
            apps = [root / name for root in (Path("/Applications"), Path.home() / "Applications")
                    for name in ("ChatGPT.app", "Codex.app")]
            for root in (Path("/Applications"), Path.home() / "Applications"):
                apps.extend(root.glob("ChatGPT*.app"))
                apps.extend(root.glob("Codex*.app"))
            apps.extend(_registered_mac_apps(check))
            for app in dict.fromkeys(apps):
                for suffix in ("Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex",
                               "Contents/Resources/codex", "Contents/MacOS/codex"):
                    raw.append((app / suffix, "desktop"))
        elif windows and env.get("LOCALAPPDATA"):
            root = Path(env["LOCALAPPDATA"])
            raw.extend((p, "desktop") for p in sorted((root / "OpenAI/Codex/bin").glob("*/codex.exe"))
                       if not p.parent.name.startswith(".") and "staging" not in p.parent.name.lower())
        if env.get("CODEX_INSTALL_DIR"):
            raw.append((Path(env["CODEX_INSTALL_DIR"]).expanduser() / executable, "install_dir"))
        for directory in env.get("PATH", "").split(";" if windows else os.pathsep):
            path = Path(directory.strip('"'))
            if not directory or not path.is_absolute():
                continue
            for name in (("codex.exe", "codex.cmd", "codex.ps1") if windows else ("codex",)):
                raw.append((path / name, "path"))
        if windows:
            if env.get("LOCALAPPDATA"):
                raw.append((Path(env["LOCALAPPDATA"]) / "Programs/OpenAI/Codex/bin/codex.exe", "standalone"))
            if env.get("APPDATA"):
                raw.append((Path(env["APPDATA"]) / "npm/codex.cmd", "npm"))
        else:
            raw.extend((Path(p), source) for p, source in (
                ("/opt/homebrew/bin/codex", "homebrew"), ("/usr/local/bin/codex", "homebrew"),
                (str(Path.home() / ".local/bin/codex"), "standalone"),
                (str(Path.home() / ".npm-global/bin/codex"), "npm")))
    result, seen = [], set()
    for path, source in raw:
        if check:
            check()
        try:
            if not path.is_file():
                if explicit:
                    result.append(Candidate(str(path), source, "codex_invalid_path"))
                continue
            native, issue = _npm_native(path, system, arch)
            normalized = str(native.resolve())
            identity = normalized.casefold() if windows else normalized
            if identity in seen:
                continue
            seen.add(identity)
            if not windows and not os.access(native, os.X_OK):
                issue = "codex_permission_denied"
            result.append(Candidate(normalized, source, issue))
        except (OSError, ValueError):
            if explicit:
                result.append(Candidate(str(path), source, "codex_invalid_path"))
    return result


class CodexSelection:
    """Lazy selection shared by all translation entrypoints, pinned for each call."""
    def __init__(self, cli_path=None, *, proxy_mode=None, proxy_url=None):
        self.cli_path = cli_path
        self.proxy_mode, self.proxy_url = proxy_mode, proxy_url
        self._lock = threading.RLock()
        self._selected = None
        self._runtime = None
        self._expires = 0
        self._signature = None
        self._leases = {}
        self._retired = set()
        self._closed = False

    def _select(self, check=None, force=False):
        from codex_client import CodexClient, CodexError, _read_version, _checked_lock, codex_environment
        deadline = time.monotonic() + DISCOVERY_SECONDS
        attempts = []
        last_error = None

        def total_check():
            if check:
                check()
            if time.monotonic() >= deadline:
                error = CodexError("codex_timeout", "Codex 自动检测超时，请重新检测或手动指定 CLI 路径。", 504)
                error.attempts = list(attempts)
                raise error

        with _checked_lock(self._lock, total_check):
            if self._closed:
                raise CodexError("codex_process_exited", "Codex 服务已关闭。", 503)
            # Re-enumerate cheap paths to detect App updates even during the cache TTL.
            candidates = collect_candidates(self.cli_path, check=total_check)
            signature = (environment_identity(), tuple((c.source, c.issue, file_identity(c.path)) for c in candidates))
            if (not force and self._selected and signature == self._signature
                    and time.monotonic() < self._expires):
                transport = self._selected._transport
                if transport and not transport._failure:
                    return self._selected
            self._expires = 0
            env = codex_environment(self.proxy_mode, self.proxy_url)
            versions = {}
            spent = {}

            def candidate_check(candidate):
                start = time.monotonic()
                remaining = CANDIDATE_SECONDS - spent.get(candidate.path, 0)
                def bounded():
                    total_check()
                    if time.monotonic() - start >= remaining:
                        raise CodexError("codex_timeout", "该 Codex 程序检测超时。", 504)
                return bounded, start

            def failure(candidate, error):
                nonlocal last_error
                last_error = error
                attempts.append({"path": candidate.path, "source": candidate.source,
                                 "code": error.code, "message": str(error)})
                if error.code not in FALLBACK_CODES:
                    error.attempts = list(attempts)
                    raise error

            def version(candidate):
                bound, start = candidate_check(candidate)
                try:
                    if candidate.issue:
                        labels = {"codex_invalid_path": "指定的 Codex CLI 路径不存在或不可读取。",
                                  "codex_permission_denied": "Codex CLI 没有执行权限。",
                                  "codex_dependency_missing": "Codex 安装缺少匹配架构的原生程序，请修复该安装。"}
                        raise CodexError(candidate.issue, labels[candidate.issue])
                    versions[candidate.path] = _read_version(candidate.path, bound, env=env)
                    return True
                except CodexError as error:
                    failure(candidate, error)
                    return False
                finally:
                    spent[candidate.path] = time.monotonic() - start

            # Desktop bundles can include alpha builds and hashed update directories.
            desktops = [c for c in candidates if c.source == "desktop" and version(c)]
            desktops.sort(key=lambda c: version_key(versions[c.path]), reverse=True)
            ordered = desktops + [c for c in candidates if c.source != "desktop"]
            for candidate in ordered:
                total_check()
                if candidate.path not in versions and not version(candidate):
                    continue
                bound, _ = candidate_check(candidate)
                client = None
                try:
                    client = CodexClient(candidate.path, proxy_mode=self.proxy_mode, proxy_url=self.proxy_url)
                    client.version = versions[candidate.path]
                    client._environment = env.copy()
                    client._ensure_started(bound)
                    bound()
                except CodexError as error:
                    if client:
                        client.close()
                    failure(candidate, error)
                    continue
                except (KeyError, TypeError, ValueError, AttributeError):
                    if client:
                        client.close()
                    failure(candidate, CodexError("codex_protocol_error", "Codex CLI 握手响应格式不兼容。", 502))
                    continue
                except BaseException:
                    if client:
                        client.close()
                    raise
                old = self._selected
                self._selected = client
                self._runtime = {"path": candidate.path, "source": candidate.source,
                                 "version": client.version, "compatible": True, "attempts": attempts}
                self._signature, self._expires = signature, time.monotonic() + CACHE_SECONDS
                if old:
                    self._retire(old)
                return client
            if attempts:
                last = attempts[-1]
                suffix = " 清空 CLI 路径可恢复自动查找。" if self.cli_path else " 未找到兼容的 Codex，请查看检测详情。"
                error = CodexError(last["code"], last["message"] + suffix, last_error.status_code)
            else:
                error = CodexError("codex_not_installed", "未找到 Codex。请安装桌面 App 或 CLI，或填写 CLI 完整路径。")
            error.attempts = attempts
            raise error

    def _retire(self, client):
        if self._leases.get(client, 0):
            self._retired.add(client)
        else:
            client.close()

    @contextmanager
    def _lease(self, check=None, force=False):
        # Reentrant lock makes selection + lease atomic against refresh/close.
        from codex_client import _checked_lock, _preparation_check
        bounded = _preparation_check(check)
        with _checked_lock(self._lock, bounded):
            client = self._select(bounded, force)
            self._leases[client] = self._leases.get(client, 0) + 1
            runtime = self._runtime
        try:
            yield client, runtime
        finally:
            with self._lock:
                self._leases[client] -= 1
                if not self._leases[client]:
                    del self._leases[client]
                    if client in self._retired:
                        self._retired.remove(client)
                        client.close()

    def discover(self, *, force=False, check_cancelled=None):
        with self._lease(check_cancelled, force) as (_, runtime):
            return {**runtime, "attempts": list(runtime["attempts"])}

    def list_models(self, *, check_cancelled=None):
        with self._lease(check_cancelled) as (client, _):
            return client.list_models(check_cancelled=check_cancelled)

    def model_catalog(self, *, check_cancelled=None):
        with self._lease(check_cancelled) as (client, runtime):
            return client.list_models(check_cancelled=check_cancelled), runtime

    def check_ready(self, model, reasoning_effort=None, *, check_cancelled=None):
        with self._lease(check_cancelled) as (client, runtime):
            return {**client.check_ready(model, reasoning_effort, check_cancelled=check_cancelled),
                    "codexRuntime": runtime}

    def translate(self, prompt, **options):
        with self._lease(options.get("check_cancelled")) as (client, _):
            return client.translate(prompt, **options)

    def session(self):
        return CodexSession(self)

    @property
    def runtime(self):
        with self._lock:
            return dict(self._runtime) if self._runtime else None

    def close(self):
        with self._lock:
            self._closed = True
            # Service shutdown, unlike refresh, must stop even leased processes.
            for client in self._retired | ({self._selected} if self._selected else set()):
                client.close()
            self._retired.clear()
            self._selected = None


class CodexSession:
    """Pin one installation for the translator's lifetime, including a whole PDF.

    The lease does not reference this wrapper, so finalization releases retired
    processes as soon as the owning translator is discarded. No request is replayed.
    """
    def __init__(self, selection):
        self._selection = selection
        self._lock = threading.Lock()
        self._client = self._runtime = self._release = None

    def _get(self, check=None):
        from codex_client import _checked_lock, _preparation_check
        bounded = _preparation_check(check)
        with _checked_lock(self._lock, bounded):
            if self._client is None:
                lease = self._selection._lease(bounded)
                self._client, self._runtime = lease.__enter__()
                self._release = weakref.finalize(self, lease.__exit__, None, None, None)
            return self._client

    @property
    def runtime(self):
        return dict(self._runtime) if self._runtime else None

    def check_ready(self, model, reasoning_effort=None, *, check_cancelled=None):
        client = self._get(check_cancelled)
        return {**client.check_ready(model, reasoning_effort, check_cancelled=check_cancelled),
                "codexRuntime": self.runtime}

    def translate(self, prompt, **options):
        return self._get(options.get("check_cancelled")).translate(prompt, **options)


# Explicit cancellation is needed because Flask cannot observe an aborted POST
# while it is probing a child. Tombstones cover cancel-before-register races.
_request_lock = threading.Lock()
_requests = {}


def _request_id(value):
    from codex_client import CodexError
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", value):
        raise CodexError("codex_invalid_request", "无效的 Codex 检测请求标识。")
    return value


def _prune_requests():
    now = time.monotonic()
    for key, (event, expires, active) in list(_requests.items()):
        if not active and expires < now:
            del _requests[key]


def cancel_discovery(request_id):
    from codex_client import CodexError
    key = _request_id(request_id)
    with _request_lock:
        _prune_requests()
        if key not in _requests:
            if len(_requests) >= 256:
                raise CodexError("codex_busy", "Codex 检测请求过多，请稍后重试。", 429)
            _requests[key] = (threading.Event(), time.monotonic() + 60, False)
        _requests[key][0].set()


@contextmanager
def discovery_request(request_id=None):
    from codex_client import CodexError
    if request_id is None:
        yield None
        return
    key = _request_id(request_id)
    with _request_lock:
        _prune_requests()
        existing = _requests.get(key)
        if existing and existing[2]:
            raise CodexError("codex_busy", "该 Codex 检测请求正在执行。", 409)
        if not existing and len(_requests) >= 256:
            raise CodexError("codex_busy", "Codex 检测请求过多，请稍后重试。", 429)
        event = existing[0] if existing else threading.Event()
        _requests[key] = (event, time.monotonic() + 60, True)
    def check():
        if event.is_set():
            raise CodexError("codex_cancelled", "Codex 检测已取消。", 499)
    try:
        check()
        yield check
    finally:
        with _request_lock:
            _requests[key] = (event, time.monotonic() + 60, False)
