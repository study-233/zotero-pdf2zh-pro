#!/usr/bin/env python3
"""Isolated local development on macOS and Windows; no installation writes."""
from __future__ import annotations

import argparse
import configparser
import contextlib
import functools
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "extensions.zotero.pdf2zhpro."
MODEL_KEYS = {PREFIX + key for key in (
    "llmApis", "selectedApiKey", "profileSchemaVersion", "service", "serviceSelect",
)}
PREF = re.compile(r'^user_pref\(("(?:[^"\\]|\\.)*"),\s*(.*?)\);\s*$')
ACTIVE = {"queued", "running", "cancelling"}
PAPERS = [
    {"id": "attention", "title": "Attention Is All You Need", "arxiv": "1706.03762v7", "testPage": 3},
    {"id": "bert", "title": "BERT", "arxiv": "1810.04805v2", "testPage": 3},
    {"id": "resnet", "title": "Deep Residual Learning for Image Recognition", "arxiv": "1512.03385v1", "testPage": 4},
]


class DevError(Exception):
    pass


def read_prefs(path):
    """Parse only JSON values in literal user_pref calls. Never evaluate JavaScript."""
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            match = PREF.fullmatch(line.strip())
            if match:
                try:
                    values[json.loads(match[1])] = json.loads(match[2])
                except (ValueError, TypeError):
                    raise DevError("无法解析 Profile 配置；未输出原文以保护密钥。") from None
    return values


def private_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8") as output:
        if sys.platform == "win32":
            windows_private(temp)
        else:
            os.chmod(temp, 0o600)
        output.write(text)
    temp.replace(path)


def write_json(path, value):
    private_write(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def update_prefs(path, updates, remove=()):
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    kept = []
    for line in lines:
        match = PREF.fullmatch(line.strip())
        if match and json.loads(match[1]) in set(updates) | set(remove):
            continue
        kept.append(line)
    for key, value in sorted(updates.items()):
        kept.append(f"user_pref({json.dumps(key)}, {json.dumps(value, ensure_ascii=True)});")
    private_write(path, "\n".join(kept) + "\n")


def overlap(first, second):
    first, second = first.resolve(), second.resolve()
    return first == second or first in second.parents or second in first.parents


@functools.lru_cache(maxsize=1)
def windows_sid():
    import csv
    result = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"],
                            capture_output=True, check=True)
    # Native Windows tools do not inherit Python's UTF-8 mode. Only the SID
    # column is used; it is ASCII regardless of the username's local encoding.
    rows = list(csv.reader(result.stdout.decode("ascii", errors="replace").splitlines()))
    if len(rows) != 1 or len(rows[0]) != 2 or not re.fullmatch(r"S-\d+(?:-\d+)+", rows[0][1]):
        raise DevError("无法确认当前 Windows 用户 SID，未修改文件权限。")
    return rows[0][1]


def windows_private(path):
    """Remove inherited access before writing credentials; protect child files too."""
    rights = "(OI)(CI)F" if path.is_dir() else "F"
    subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r",
                    f"*{windows_sid()}:{rights}", f"*S-1-5-18:{rights}"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)


def linked(path):
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def directory_links(folder):
    """Do not descend through junctions (Path.rglob does on Windows)."""
    pending = [folder]
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                is_dir = entry.is_dir(follow_symlinks=False)
                path = Path(entry.path)
                if entry.is_symlink() or (is_dir and linked(path)):
                    yield path
                elif is_dir:
                    pending.append(path)


def same_path(first, second):
    return Path(first).resolve() == Path(second).resolve()


class MacOS:
    """All OS process/discovery/locking behavior lives in this adapter."""
    @staticmethod
    def processes():
        result = subprocess.run(
            ["ps", "-ww", "-axo", "pid=,ppid=,lstart=,command="],
            capture_output=True, text=True, check=True, env={**os.environ, "LC_ALL": "en_US.UTF-8"},
        )
        rows = {}
        for line in result.stdout.splitlines():
            fields = line.strip().split(None, 7)
            if len(fields) == 8:
                pid, parent = int(fields[0]), int(fields[1])
                rows[pid] = {"pid": pid, "parent": parent, "started": " ".join(fields[2:7]), "command": fields[7]}
        return rows

    def zotero(self):
        return [p for p in self.processes().values()
                if re.match(r"^.*?/Zotero\.app/Contents/MacOS/zotero(?:\s|$)", p["command"])]

    def require_closed(self):
        if self.zotero():
            raise DevError("请先正常退出 Zotero，再执行此命令。不会自动关闭正式版。")

    @staticmethod
    def listeners(port):
        result = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                                capture_output=True, text=True, check=True)
        return set(result.stdout.split())

    def owned(self, record):
        if not record:
            return False
        current = self.processes().get(record["pid"])
        return bool(current and all(current[k] == record[k] for k in ("started", "command")))

    def stop(self, record):
        if not self.owned(record):
            return
        os.kill(record["pid"], signal.SIGTERM)
        for _ in range(100):
            if not self.owned(record):
                return
            time.sleep(0.1)
        raise DevError("开发进程未正常退出；未强制结束，请查看日志。")

    @staticmethod
    def source_profile():
        base = Path.home() / "Library/Application Support/Zotero"
        config = configparser.ConfigParser()
        config.read(base / "profiles.ini")
        profiles = [s for s in config.sections() if s.startswith("Profile")]
        defaults = [s for s in profiles if config.getboolean(s, "Default", fallback=False)]
        selected = defaults or profiles
        if len(selected) != 1:
            raise DevError("无法唯一确定正式 Profile，请用 init --source-profile 指定路径。")
        section = selected[0]
        path = Path(config.get(section, "Path"))
        return (base / path if config.getboolean(section, "IsRelative", fallback=True) else path).resolve()

    @staticmethod
    @contextlib.contextmanager
    def lock(path):
        import fcntl
        with path.open("a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise DevError("另一个开发管理命令正在执行，请稍后重试。") from None
            yield


class Windows:
    """Use argument arrays and process identities, never image-name termination."""
    @staticmethod
    def processes():
        import psutil
        # process_iter caches Process objects (including creation time). Refresh
        # before every ownership decision so a recycled PID cannot inherit it.
        psutil.process_iter.cache_clear()
        rows = {}
        for process in psutil.process_iter():
            try:
                with process.oneshot():
                    argv = process.cmdline()
                    rows[process.pid] = {
                        "pid": process.pid, "parent": process.ppid(),
                        "started": process.create_time(), "exe": process.exe(),
                        "argv": argv, "command": subprocess.list2cmdline(argv),
                    }
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return rows

    def zotero(self):
        return [p for p in self.processes().values()
                if Path(p["exe"]).name.lower() == "zotero.exe"]

    def require_closed(self):
        # init also works with a stdlib-only bootstrap Python, before uv sync.
        result = subprocess.run(["tasklist", "/fi", "IMAGENAME eq zotero.exe", "/fo", "csv", "/nh"],
                                capture_output=True, check=True)
        # Match the ASCII image name directly, without decoding localized
        # messages such as the GBK "no matching tasks" response.
        if b'"zotero.exe"' in result.stdout.lower():
            raise DevError("请先正常退出 Zotero，再执行此命令。不会自动关闭正式版。")

    @staticmethod
    def listeners(port):
        import psutil
        return {str(c.pid) for c in psutil.net_connections(kind="tcp")
                if c.status == psutil.CONN_LISTEN and c.laddr.port == port}

    def owned(self, record):
        if not record:
            return False
        current = self.processes().get(record["pid"])
        return bool(current and all(current.get(k) == record.get(k)
                                    for k in ("started", "exe", "argv")))

    def stop(self, record):
        import psutil
        if not self.owned(record):
            return
        try:
            process = psutil.Process(record["pid"])
            if process.create_time() != record["started"] or not self.owned(record):
                return
            process.terminate()
            process.wait(timeout=10)
        except psutil.NoSuchProcess:
            return
        except psutil.TimeoutExpired:
            raise DevError("开发进程未退出；保留进程记录，请查看日志。") from None

    @staticmethod
    def source_profile():
        base = Path(os.environ["APPDATA"]) / "Zotero/Zotero"
        config = configparser.ConfigParser()
        config.read(base / "profiles.ini", encoding="utf-8")
        profiles = [s for s in config.sections() if s.startswith("Profile")]
        defaults = [s for s in profiles if config.getboolean(s, "Default", fallback=False)]
        selected = defaults or profiles
        if len(selected) != 1:
            raise DevError("无法唯一确定正式 Profile，请用 init --source-profile 指定路径。")
        section = selected[0]
        path = Path(config.get(section, "Path"))
        return (base / path if config.getboolean(section, "IsRelative", fallback=True) else path).resolve()

    @staticmethod
    def binary():
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for flag in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\zotero.exe",
                                        access=winreg.KEY_READ | flag) as key:
                        path = Path(winreg.QueryValue(key, None).strip('"'))
                        if path.is_file():
                            return path
                except OSError:
                    pass
        for folder in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            if os.environ.get(folder):
                path = Path(os.environ[folder]) / "Zotero/zotero.exe"
                if path.is_file():
                    return path
        raise DevError("找不到 Zotero 程序，请使用 init --zotero-bin 指定。")

    @staticmethod
    @contextlib.contextmanager
    def lock(path):
        import msvcrt
        with path.open("a+b") as handle:
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise DevError("另一个开发管理命令正在执行，请稍后重试。") from None
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


class Development:
    def __init__(self, root=ROOT, platform=None):
        self.root = root.resolve()
        self.runtime = self.root / ".local-dev/runtime"
        self.platform = platform or (Windows() if sys.platform == "win32" else MacOS())
        self.config_path = self.runtime / "environment.json"
        self.state_path = self.runtime / "processes.json"
        self.url = "http://127.0.0.1:8891"

    def paths(self):
        return {name: self.runtime / name for name in (
            "profile", "library", "tasks", "config", "cache", "logs", "samples", "backups", "plugin-build",
        )}

    def config(self):
        if not self.config_path.exists():
            raise DevError("开发环境尚未初始化，请先运行 scripts/dev.ps1 init（Windows）或 scripts/dev.sh init（macOS）。")
        return json.loads(self.config_path.read_text(encoding="utf-8"))

    def validate_paths(self, config):
        source = Path(config["sourceProfile"]).resolve()
        prefs = read_prefs(source / "prefs.js")
        prefs.update(read_prefs(source / "user.js"))
        formal_data = (Path(prefs["extensions.zotero.dataDir"]).expanduser()
                       if prefs.get("extensions.zotero.useDataDir") and prefs.get("extensions.zotero.dataDir")
                       else Path.home() / "Zotero")
        if overlap(self.runtime, source) or overlap(self.runtime, formal_data):
            raise DevError("开发目录与正式 Profile 或文献库重叠，拒绝操作。")
        # Refuse linked runtime components, including files: private writes must never escape.
        expected = self.root / ".local-dev" / "runtime"
        if self.runtime.resolve() != expected or linked(self.root / ".local-dev") or linked(self.runtime):
            raise DevError("开发根目录不能是符号链接。")
        if self.runtime.exists():
            for path in directory_links(self.runtime):
                # uv creates this version alias; allow only a sibling interpreter
                # inside our private Python installation, never a data-directory alias.
                if (sys.platform == "win32" and path.parent == self.runtime / "python"
                        and re.fullmatch(r"cpython-3\.13-windows-[\w_-]+", path.name)
                        and path.resolve().parent == self.runtime / "python"
                        and re.fullmatch(r"cpython-3\.13\.\d+-windows-[\w_-]+", path.resolve().name)):
                    continue
                raise DevError("开发目录包含符号链接或目录联接，拒绝操作以保护目录隔离。")
        paths = list(self.paths().values())
        for path in paths:
            if overlap(path, source) or overlap(path, formal_data):
                raise DevError("开发子目录与正式环境重叠。")
        if source == self.paths()["profile"].resolve():
            raise DevError("模型配置来源不能是开发 Profile。")

    def env(self):
        paths = self.paths()
        env = dict(os.environ)
        env.update({
            "PDF2ZH_DEV_RUNTIME": str(self.runtime),
            "PDF2ZH_TRANSLATION_CACHE_DIR": str(paths["cache"]),
            "PDF2ZH_CONFIG_DIR": str(paths["config"]),
            "PDF2ZH_DATA_DIR": str(paths["tasks"]),
            "PDF2ZH_LOG_FILE": str(paths["logs"] / "server.log"),
            "ZOTERO_PLUGIN_ZOTERO_BIN_PATH": self.config()["zoteroBin"],
            "ZOTERO_PLUGIN_PROFILE_PATH": str(paths["profile"]),
            "ZOTERO_PLUGIN_DATA_DIR": str(paths["library"]),
            # The scaffold also calls its broad kill helper on exit. Disable that helper.
            "ZOTERO_PLUGIN_KILL_COMMAND": "exit /b 0" if sys.platform == "win32" else "/usr/bin/true",
            "NODE_ENV": "development",
            "PYTHONUTF8": "1",
        })
        if sys.platform == "win32":
            env.update({
                "UV_PROJECT_ENVIRONMENT": str(self.runtime / "venv"),
                "UV_CACHE_DIR": str(self.runtime / "uv-cache"),
                "UV_PYTHON_INSTALL_DIR": str(self.runtime / "python"),
                "UV_TOOL_DIR": str(self.runtime / "tools"),
                "UV_TOOL_BIN_DIR": str(self.runtime / "tools/bin"),
                "UV_LINK_MODE": "copy",
            })
            env.pop("VIRTUAL_ENV", None)
        return env

    def init(self, source=None, binary=None):
        self.platform.require_closed()
        config = self.config() if self.config_path.exists() else {
            "sourceProfile": str((source or self.platform.source_profile()).resolve()),
            "zoteroBin": str(binary or (Windows.binary() if sys.platform == "win32"
                                       else Path("/Applications/Zotero.app/Contents/MacOS/zotero"))),
        }
        self.validate_paths(config)
        if not (Path(config["sourceProfile"]) / "prefs.js").is_file():
            raise DevError("正式 Profile 缺少 prefs.js。")
        if not Path(config["zoteroBin"]).is_file():
            raise DevError("找不到 Zotero 程序，请使用 init --zotero-bin 指定。")
        for command in (("pnpm",) if sys.platform == "win32" else ("uv", "pnpm")):
            if not shutil.which(command):
                raise DevError(f"缺少 {command}，请先安装后重试。")
        for path in self.paths().values():
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
        write_json(self.config_path, config)
        uv = str(self.runtime / "tools/uv.exe") if sys.platform == "win32" else "uv"
        if sys.platform == "win32" and not Path(uv).is_file():
            raise DevError("请将独立 uv.exe 放入 .local-dev/runtime/tools/uv.exe 后重试；不会调用正式安装的 uv。")
        subprocess.run([uv, "sync", "--directory", str(self.root / "server"), "--locked",
                        *(["--python", "3.13", "--managed-python"] if sys.platform == "win32" else [])],
                       env=self.env(), check=True)
        subprocess.run([shutil.which("pnpm.cmd") or "pnpm", "--dir", str(self.root / "plugin"),
                        "install", "--frozen-lockfile"], env={**self.env(), "CI": "true"}, check=True)
        if not (self.runtime / "models-initialized.json").exists():
            self.sync_models()
        self.set_dev_prefs()
        print("开发环境已初始化；已有测试数据保留。")

    def set_dev_prefs(self):
        paths = self.paths()
        update_prefs(paths["profile"] / "user.js", {
            PREFIX + "new_serverip": self.url,
            "extensions.zotero.dataDir": str(paths["library"]),
            "extensions.zotero.useDataDir": True,
            "extensions.update.enabled": False,
            "extensions.update.autoUpdateDefault": False,
            "extensions.zotero.sync.autoSync": False,
            "extensions.zotero.firstRun": False,
            "browser.shell.checkDefaultBrowser": False,
            "devtools.debugger.prompt-connection": False,
            "devtools.debugger.force-local": True,
        })

    def sync_models(self):
        self.platform.require_closed()
        config = self.config()
        self.validate_paths(config)
        if not (Path(config["sourceProfile"]) / "prefs.js").is_file():
            raise DevError("正式 Profile 的 prefs.js 不存在，开发配置未更改。")
        source = read_prefs(Path(config["sourceProfile"]) / "prefs.js")
        # user.js has precedence at Zotero startup, so respect explicit source overrides.
        source.update(read_prefs(Path(config["sourceProfile"]) / "user.js"))
        selected = {k: v for k, v in source.items() if k in MODEL_KEYS}
        try:
            profiles = json.loads(selected.get(PREFIX + "llmApis", "[]"))
            if not isinstance(profiles, list) or any(not isinstance(p, dict) for p in profiles):
                raise ValueError()
        except (ValueError, TypeError):
            raise DevError("正式模型列表格式无效，开发配置未更改。") from None
        target = self.paths()["profile"] / "prefs.js"
        if target.exists():
            private_write(self.paths()["backups"] / f"models-{time.time_ns()}.js", target.read_text(encoding="utf-8"))
        update_prefs(target, selected, MODEL_KEYS | {PREFIX + "llmApisLegacyBackup"})
        # Do not pin model settings in user.js; edits inside the developer UI must persist.
        update_prefs(target.with_name("user.js"), {}, MODEL_KEYS)
        self.set_dev_prefs()
        write_json(self.runtime / "models-initialized.json", {"copiedAt": time.time(), "count": len(profiles)})
        print(f"已复制 {len(profiles)} 个模型配置；密钥未输出。")

    def state(self):
        return json.loads(self.state_path.read_text(encoding="utf-8")) if self.state_path.exists() else {}

    def record(self, pid):
        for _ in range(30):
            process = self.platform.processes().get(pid)
            if process:
                return process
            time.sleep(0.1)
        raise DevError("开发进程未启动，请查看日志。")

    def launch(self, command, log, cwd):
        with log.open("ab") as output:
            options = ({"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32"
                       else {"start_new_session": True})
            proc = subprocess.Popen(command, cwd=cwd, env=self.env(), stdin=subprocess.DEVNULL,
                                    stdout=output, stderr=output, **options)
        return self.record(proc.pid)

    def request(self, route):
        # Local traffic must not use an inherited HTTP proxy.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(self.url + route, timeout=3) as response:
            return json.load(response)

    def port_free(self):
        with socket.socket() as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE if sys.platform == "win32"
                            else socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", 8891))
            except OSError:
                raise DevError("端口 8891 已被占用；未结束任何进程。") from None

    def check_server(self, state):
        if not self.platform.owned(state.get("server")):
            raise DevError("开发后端进程不存在或身份变化；拒绝操作未知服务。")
        health = self.request("/health")
        if Path(health.get("workspace", {}).get("path", "")).resolve() != self.paths()["tasks"]:
            raise DevError("后端任务目录不属于开发环境。")
        # Verify the actual listener, not just a surviving parent process and a health response.
        if self.platform.listeners(8891) != {str(state["server"]["pid"])}:
            raise DevError("8891 监听者与开发后端 PID 不匹配。")
        return health

    def require_idle(self, state):
        if state.get("server") and not self.platform.owned(state["server"]):
            if self.platform.listeners(8891):
                raise DevError("开发进程记录失效且端口仍被占用；拒绝停止或重启。")
            return
        if state.get("server") and self.platform.owned(state["server"]):
            self.check_server(state)
            tasks = self.request("/tasks").get("tasks")
            if not isinstance(tasks, list):
                raise DevError("无法确认任务状态，拒绝停止。")
            if any(t.get("status") in ACTIVE for t in tasks):
                raise DevError("仍有开发翻译任务，请先完成或取消，再停止或重启。")

    def start_server(self, state):
        self.port_free()
        python = self.runtime / "venv/Scripts/python.exe" if sys.platform == "win32" else self.root / "server/.venv/bin/python"
        command = [str(python),
                   str(self.root / "server/service_launcher.py"), "--host", "127.0.0.1",
                   "--port", "8891", "--data-dir", str(self.paths()["tasks"])]
        state["server"] = self.launch(command, self.paths()["logs"] / "server-console.log", self.root)
        write_json(self.state_path, state)
        for _ in range(90):
            if not self.platform.owned(state["server"]):
                break
            try:
                if isinstance(self.platform, Windows):
                    self.adopt_server_listener(state)
                self.check_server(state)
                return
            except (OSError, ValueError, DevError, subprocess.CalledProcessError):
                time.sleep(0.5)
        raise DevError("开发后端未通过健康检查，请查看 server-console.log。")

    def adopt_server_listener(self, state):
        """Windows venv python.exe may delegate to its base interpreter."""
        listeners = self.platform.listeners(8891)
        if listeners == {str(state["server"]["pid"])}:
            return
        if len(listeners) != 1 or not self.platform.owned(state["server"]):
            raise DevError("无法确认开发后端监听者。")
        rows = self.platform.processes()
        child = rows.get(int(next(iter(listeners))))
        if not child or child["parent"] != state["server"]["pid"] or child["argv"][1:] != state["server"]["argv"][1:]:
            raise DevError("8891 监听者不是本次启动的开发后端。")
        if not Path(child["exe"]).resolve().is_relative_to((self.runtime / "python").resolve()):
            raise DevError("开发后端解释器不属于隔离目录。")
        state["launcher"] = state["server"]
        state["server"] = child
        write_json(self.state_path, state)

    def development_zotero(self):
        if isinstance(self.platform, Windows):
            def matches(process):
                args = process["argv"]
                try:
                    return (same_path(process["exe"], self.config()["zoteroBin"])
                            and same_path(args[args.index("-profile") + 1], self.paths()["profile"])
                            and same_path(args[args.index("--dataDir") + 1], self.paths()["library"]))
                except (ValueError, IndexError):
                    return False
            return [p for p in self.platform.zotero() if matches(p)]
        marker = "-profile " + str(self.paths()["profile"])
        return [p for p in self.platform.zotero() if marker + " " in p["command"]
                and "--dataDir " + str(self.paths()["library"]) + " " in p["command"]]

    def start(self):
        self.platform.require_closed()
        self.validate_paths(self.config())
        state = self.state()
        if any(self.platform.owned(p) for p in state.values()):
            raise DevError("已有开发进程，请先运行 status 或 stop。")
        state = {}
        self.port_free()
        self.set_dev_prefs()
        ready = self.runtime / "plugin-ready.json"
        ready.unlink(missing_ok=True)
        (self.runtime / "quit-request.json").unlink(missing_ok=True)
        server_ready = False
        try:
            self.start_server(state)
            server_ready = True
            # Invoke the scaffold directly: its recorded process is the actual Node watcher.
            cli = self.root / "plugin/node_modules/zotero-plugin-scaffold/bin/zotero-plugin.mjs"
            state["watcher"] = self.launch([shutil.which("node") or "node", str(cli), "serve"],
                                          self.paths()["logs"] / "plugin.log", self.root / "plugin")
            write_json(self.state_path, state)
            for _ in range(180):
                if not self.platform.owned(state["watcher"]):
                    break
                instances = self.development_zotero()
                if len(instances) == 1:
                    state["zotero"] = instances[0]
                    write_json(self.state_path, state)
                if ready.exists() and "zotero" in state:
                    info = json.loads(ready.read_text(encoding="utf-8"))
                    if same_path(info["profile"], self.paths()["profile"]) and same_path(info["data"], self.paths()["library"]):
                        print("开发 Zotero 已就绪；插件自动加载改动，Python 改动后运行 restart-server。")
                        return
                time.sleep(0.5)
            raise DevError("开发插件未就绪，请查看 plugin.log；测试数据已保留。")
        except BaseException:
            # Capture a just-spawned Zotero before stopping the watcher.
            instances = self.development_zotero()
            if len(instances) == 1:
                state["zotero"] = instances[0]
            write_json(self.state_path, state)
            if server_ready:
                self.stop_state(state, graceful=False)
            elif "server" in state:
                # No plugin was launched or task submitted: clean up only this new server.
                self.platform.stop(state.pop("server"))
                if "launcher" in state:
                    self.platform.stop(state.pop("launcher"))
                write_json(self.state_path, state)
            raise

    def stop_state(self, state, graceful=True):
        self.require_idle(state)
        for name in ("zotero", "watcher", "server", "launcher"):
            if name in state:
                if name == "zotero" and graceful and self.platform.owned(state[name]):
                    request = self.runtime / "quit-request.json"
                    write_json(request, {"requestedAt": time.time()})
                    try:
                        for _ in range(150):
                            if not self.platform.owned(state[name]):
                                break
                            time.sleep(0.1)
                        else:
                            raise DevError("开发 Zotero 未响应正常退出请求，请手动退出后再次 stop。")
                    finally:
                        request.unlink(missing_ok=True)
                else:
                    self.platform.stop(state[name])
                del state[name]
                write_json(self.state_path, state)

    def stop(self):
        self.validate_paths(self.config())
        self.stop_state(self.state())
        print("开发进程已停止，所有开发数据保留。")

    def restart(self):
        # Reuse both lifecycle guards under the CLI's existing command lock.
        # If stopping fails (including active tasks), do not attempt to start.
        self.stop()
        self.start()

    def restart_server(self):
        self.validate_paths(self.config())
        state = self.state()
        self.require_idle(state)
        if "server" in state:
            self.platform.stop(state.pop("server"))
        if "launcher" in state:
            self.platform.stop(state.pop("launcher"))
        write_json(self.state_path, state)
        try:
            self.start_server(state)
        except BaseException:
            if "server" in state:
                self.platform.stop(state.pop("server"))
            if "launcher" in state:
                self.platform.stop(state.pop("launcher"))
                write_json(self.state_path, state)
            raise
        print("开发后端已重新加载源码。")

    def status(self):
        self.validate_paths(self.config())
        state = self.state()
        revision = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=self.root,
                                  capture_output=True, text=True, check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=self.root,
                                   capture_output=True, text=True, check=True).stdout)
        print(f"源码：{revision}" + ("（含工作区修改）" if dirty else ""))
        print(f"开发服务：{self.url}\n运行目录：{self.runtime}")
        for key in ("server", "watcher", "zotero"):
            print(f"{key}: " + (f"运行中 PID {state[key]['pid']}" if self.platform.owned(state.get(key)) else "未运行"))
        if self.platform.owned(state.get("server")):
            print("实际任务目录：" + self.check_server(state)["workspace"]["path"])

    def prepare_samples(self):
        self.validate_paths(self.config())
        folder = self.paths()["samples"]
        manifest_path = folder / "manifest.json"
        previous = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {"papers": []}
        known = {p["id"]: p for p in previous["papers"]}
        papers = []
        for paper in PAPERS:
            path = folder / (paper["id"] + ".pdf")
            url = "https://arxiv.org/pdf/" + paper["arxiv"]
            if not path.exists():
                request = urllib.request.Request(url, headers={"User-Agent": "pdf2zh-local-development/1"})
                with urllib.request.urlopen(request, timeout=90) as response:
                    data = response.read(50 * 1024 * 1024 + 1)
                if len(data) > 50 * 1024 * 1024 or not data.startswith(b"%PDF-"):
                    raise DevError("样本下载未返回有效 PDF。")
                path.write_bytes(data)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if paper["id"] in known and known[paper["id"]]["sha256"] != digest:
                raise DevError(f"{paper['id']} 样本校验值变化，请保留旧文件后核查来源。")
            papers.append({**paper, "source": url, "sha256": digest})
            print(f"样本就绪：{paper['title']}（快速测试第 {paper['testPage']} 页）")
        write_json(manifest_path, {
            "benchmarkCommit": "ec8001d346856baad9dcb263e3aa2c3d9af69c09", "papers": papers,
        })
        print("下次启动开发 Zotero 时自动导入“开发测试”分类；不会发起翻译。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "start", "status", "stop", "restart", "restart-server", "sync-models", "prepare-samples"))
    parser.add_argument("--source-profile", type=Path, help="首次 init 时的正式 Profile 路径")
    parser.add_argument("--zotero-bin", type=Path, help="首次 init 时的 Zotero 可执行文件路径")
    args = parser.parse_args()
    if sys.platform not in ("darwin", "win32"):
        parser.exit(1, "当前仅支持 macOS 和 Windows。\n")
    os.umask(0o077)
    dev = Development()
    try:
        # Validate before creating even the lock file.
        config = dev.config() if dev.config_path.exists() else {
            "sourceProfile": str((args.source_profile or dev.platform.source_profile()).resolve()),
        }
        dev.validate_paths(config)
        dev.runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
        if sys.platform == "win32":
            windows_private(dev.runtime)
        with dev.platform.lock(dev.runtime / "command.lock"):
            if args.command == "init":
                dev.init(args.source_profile, args.zotero_bin)
            else:
                getattr(dev, args.command.replace("-", "_"))()
        return 0
    except (DevError, OSError, ValueError, subprocess.CalledProcessError) as error:
        # Unexpected parser/process failures must not echo source preferences or environment.
        message = str(error) if isinstance(error, DevError) else type(error).__name__ + "；请查看开发日志。"
        print("dev: " + message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
