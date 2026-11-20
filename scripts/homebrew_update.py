"""Manage the official Homebrew backend. Invoked by Zotero with a fixed prefix and release."""
import argparse
import fcntl
import http.client
import json
import os
from pathlib import Path
import plistlib
import platform
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time

NAME = "zotero-pdf2zh-pro"
TAP = "study-233/formula"
FORMULA = f"{TAP}/{NAME}"
PORT = 8890
ACTIVE = {"stopping", "backup", "installing", "verifying", "restoring"}
VERSION = re.compile(r"\d+\.\d+\.\d+")


class Unavailable(Exception):
    pass


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path, data):
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as stream:
        json.dump(data, stream)
        stream.flush()
        os.fsync(stream.fileno())
    tmp.replace(path)


def parsed(version):
    return tuple(map(int, version.split(".")))


def request(method="GET", token=None, idle=60):
    # Direct loopback connection: never proxy or redirect a maintenance credential.
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=3)
    try:
        headers = {"X-PDF2ZH-Control-Token": token, "Content-Type": "application/json"} if token else {}
        conn.request(method, "/internal/maintenance" if token else "/health",
                     json.dumps({"idleSeconds": idle}) if token else None, headers)
        response = conn.getresponse()
        return response.status, json.loads(response.read(262144))
    finally:
        conn.close()


def port_open():
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=2):
            return True
    except ConnectionRefusedError:
        return False


class Backend:
    def __init__(self, prefix, target):
        self.prefix = prefix
        self.opt = prefix / "opt" / NAME
        self.rack = prefix / "Cellar" / NAME
        self.data = prefix / "var" / NAME
        self.work = prefix / "var" / f".{NAME}-updater"
        self.journal = self.work / "state.json"
        self.target = target
        self.worker_fd = None

    def brew(self, *args, timeout=120):
        env = {**os.environ, "HOMEBREW_NO_AUTO_UPDATE": "1", "HOMEBREW_NO_INSTALL_CLEANUP": "1",
               "HOMEBREW_NO_INSTALLED_DEPENDENTS_CHECK": "1", "HOMEBREW_NO_ASK": "1",
               "HOMEBREW_NO_ENV_HINTS": "1", "HOMEBREW_NO_COLOR": "1", "GIT_TERMINAL_PROMPT": "0",
               "PATH": f"{self.prefix}/bin:/usr/bin:/bin:/usr/sbin:/sbin"}
        # Installation output can be large. Only the two JSON commands need stdout.
        result = subprocess.run([self.prefix / "bin/brew", *args], env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE if "--json" in args or "--json=v2" in args else subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, check=True, timeout=timeout,
                                pass_fds=(self.worker_fd,) if self.worker_fd is not None else ())
        return json.loads(result.stdout) if result.stdout else None

    def formula(self):
        keg = self.opt.resolve(strict=True)
        if keg.parent != self.rack or read_json(keg / "INSTALL_RECEIPT.json")["source"]["tap"] != TAP:
            raise Unavailable()
        info, = self.brew("info", "--json=v2", FORMULA)["formulae"]
        if info["tap"] != TAP or info["name"] != NAME or keg.name not in [i["version"] for i in info["installed"]]:
            raise Unavailable()
        # A Homebrew formula revision (e.g. 1.9.8_1) is not a backend version.
        version = keg.name.split("_")[0]
        if not VERSION.fullmatch(version):
            raise Unavailable()
        return keg, version, info

    def service(self):
        svc, = self.brew("services", "info", "--json", FORMULA)
        if svc["name"] != NAME or svc.get("user") == "root" or svc.get("service_name") not in {
            "sh.brew." + NAME, "homebrew.mxcl." + NAME
        }:
            raise Unavailable()
        return svc

    def listener(self, svc):
        pid = svc.get("pid")
        return bool(isinstance(pid, int) and pid > 0 and subprocess.run(
            ["/usr/sbin/lsof", "-nP", "-a", "-p", str(pid), f"-iTCP:{PORT}", "-sTCP:LISTEN"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5).returncode == 0)

    def health(self, svc):
        if not self.listener(svc):
            raise Unavailable()
        status, data = request()
        if (status != 200 or data.get("status") != "ok" or data.get("managedOwner")
                or Path(data["workspace"]["path"]).resolve() != self.data.resolve()
                or data.get("taskStore", {}).get("status") != "ok"):
            raise Unavailable()
        return data

    def inspect(self):
        keg, version, info = self.formula()
        svc = self.service()
        if self.data.is_symlink():
            raise Unavailable()
        if svc["running"]:
            health = self.health(svc)
            live = health["version"]
            if not VERSION.fullmatch(live):
                raise Unavailable()
        elif svc["loaded"] or port_open():
            raise Unavailable()
        else:
            live = None
        stable = info["versions"]["stable"]
        if platform.machine() != "arm64":
            state = "unsupported"
        elif version == self.target and live in {None, self.target}:
            state = "current"
        elif info["pinned"]:
            state = "pinned"
        elif parsed(version) > parsed(self.target) or (live and parsed(live) > parsed(self.target)):
            state = "ahead"
        elif version == self.target:
            state = "available"  # An older runtime still owns the listener.
        elif stable == self.target:
            state = "available"
        elif VERSION.fullmatch(stable) and parsed(stable) > parsed(self.target):
            state = "ahead"
        else:
            state = "unpublished"
        return dict(state=state, installed=version, running=live, directory=str(self.data)), svc, keg

    def lock(self):
        self.work.mkdir(mode=0o700, exist_ok=True)
        stream = (self.work / "update.lock").open("a+")
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            stream.close()
            raise
        return stream

    def state(self):
        return read_json(self.journal) if self.journal.exists() else {}

    def save(self, job, phase):
        job["phase"] = phase
        atomic_json(self.journal, job)

    def status(self):
        job = self.state()
        if job.get("phase") in ACTIVE:
            try:
                with self.lock():
                    job = self.state()
                    if job.get("phase") in ACTIVE:
                        return {**job["view"], "state": "interrupted"}
            except BlockingIOError:
                return {**job["view"], "state": job["phase"]}
        if job.get("phase") == "recoveryFailed":
            return {**job["view"], "state": "recoveryFailed"}
        view = self.inspect()[0]
        if job.get("target") == self.target and job.get("phase") in {"failed", "restored"}:
            if view["state"] in {"available", "current"} and (view["state"] != "current" or job["phase"] == "failed"):
                view["state"] = job["phase"]
        return view

    def maintenance(self, idle=60):
        token = (self.data / ".control-token").read_text("ascii").strip()
        if not re.fullmatch("[a-f0-9]{64}", token):
            raise Unavailable()
        status, result = request("POST", token, idle)
        if status == 409:
            return False
        if status != 200 or result.get("held") is not True:
            raise Unavailable()
        return True

    def start(self, job, file):
        if job["start"] == "run":
            # launchctl reports this path as the loaded file on subsequent upgrades.
            dest = self.work / "temporary-service.plist"
            dest.write_bytes(file.read_bytes())
            file = dest
        self.brew("services", job["start"], FORMULA, f"--file={file}")

    def wait_health(self, version):
        for _ in range(45):
            try:
                if self.health(self.service())["version"] == version:
                    return
            except (Unavailable, OSError, KeyError, ValueError, http.client.HTTPException):
                pass
            time.sleep(1)
        raise Unavailable()

    def prepare(self, svc, keg, job):
        if not svc["running"]:
            return
        # Preserve user environment, launch arguments and whether this was a temporary run.
        path = Path(svc.get("loaded_file") or svc["file"])
        config = plistlib.loads(path.read_bytes())
        args = config["ProgramArguments"]
        entry = self.opt / "bin" / NAME
        old = self.rack / job["view"]["running"] / "libexec/venv/bin" / NAME
        if keg.name.split("_")[0] == job["view"]["running"]:
            old = keg / "libexec/venv/bin" / NAME
        if (config["Label"] != svc["service_name"] or args[0] not in {str(entry), str(old)}
                or not old.is_file() or "Program" in config):
            raise Unavailable()
        directory = Path(job["backup"])
        args[0] = str(entry)
        (directory / "start.plist").write_bytes(plistlib.dumps(config))
        # The keg's bin wrapper follows opt. Pin the venv entry point for a real rollback.
        args[0] = str(old)
        (directory / "rollback.plist").write_bytes(plistlib.dumps(config))

    def stop(self):
        self.brew("services", "stop", FORMULA)
        if self.service()["loaded"] or port_open():
            raise Unavailable()

    def restore(self, job):
        directory = Path(job["backup"])
        if job["start"]:
            svc = self.service()
            if svc["running"] and self.listener(svc):
                try:
                    if self.health(svc)["version"] == job["view"]["running"]:
                        return
                except (Unavailable, ValueError, OSError, KeyError):
                    pass
                if not job.get("startedNew"):
                    raise Unavailable()
                # An unhealthy listener still needs to belong to this workspace before stopping it.
                _, health = request()
                if Path(health["workspace"]["path"]).resolve() != self.data.resolve() or not self.maintenance(0):
                    raise Unavailable()
            elif port_open():
                raise Unavailable()
            if svc["loaded"]:
                self.stop()
            backup = directory / "data"
            if job.get("startedNew") and backup.is_dir():
                # Each rename is atomic on the same filesystem. A second recovery resumes between renames.
                if not (directory / "after-update").exists() and self.data.exists():
                    self.data.rename(directory / "after-update")
                if not self.data.exists():
                    backup.rename(self.data)
            self.start(job, directory / "rollback.plist")
            self.wait_health(job["view"]["running"])

    def recover(self, job):
        # A crash after verification but before the journal commit must not roll back new work.
        try:
            svc = self.service()
            was_stopped = not job["start"] and not svc["running"] and not svc["loaded"] and not port_open()
            if was_stopped or (job.get("startedNew") and self.health(svc)["version"] == job["target"]):
                self.verify_install(job["target"])
                self.save(job, "done")
                shutil.rmtree(job["backup"], ignore_errors=True)
                return
        except (Unavailable, OSError, KeyError, ValueError, http.client.HTTPException, subprocess.SubprocessError):
            pass
        try:
            self.restore(job)
            self.save(job, "restored" if job["start"] else "failed")
        except Exception:
            self.save(job, "recoveryFailed")

    def verify_install(self, expected):
        keg, version, _ = self.formula()
        if version != expected:
            raise Unavailable()
        actual = subprocess.check_output(
            [keg / "libexec/venv/bin/python", "-I", "-c",
             "from importlib.metadata import version; print(version('zotero-pdf2zh-pro'))"],
            timeout=30, text=True, stderr=subprocess.DEVNULL).strip()
        if actual != expected:
            raise Unavailable()

    def perform(self, job):
        directory = Path(job["backup"])
        try:
            if job["start"]:
                svc = self.service()
                if svc["pid"] != job["pid"] or not self.health(svc).get("maintenance", {}).get("held"):
                    raise Unavailable()
                self.stop()
                self.save(job, "backup")
                shutil.copytree(self.data, directory / "data", symlinks=True)
            self.save(job, "installing")
            view, service, _ = self.inspect()
            if service["running"] or service["loaded"]:
                raise Unavailable()
            if view["state"] != "current":
                if view["state"] != "available":
                    raise Unavailable()
                self.brew("upgrade", FORMULA, timeout=None)
            self.save(job, "verifying")
            self.verify_install(self.target)
            if job["start"]:
                job["startedNew"] = True
                self.save(job, "verifying")
                self.start(job, directory / "start.plist")
                self.wait_health(self.target)
            self.save(job, "done")
        except Exception:
            self.save(job, "restoring")
            try:
                self.restore(job)
            except Exception:
                self.save(job, "recoveryFailed")
                return
            self.save(job, "restored" if job["start"] else "failed")
            return
        shutil.rmtree(directory, ignore_errors=True)

    def launch(self, lock, recover=False):
        # The worker owns the inherited advisory lock even if Zotero exits.
        subprocess.Popen([sys.executable, "-I", __file__, "recover" if recover else "worker",
                          str(self.prefix), self.target, str(lock.fileno())],
                         pass_fds=(lock.fileno(),), start_new_session=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def upgrade(self, manual):
        try:
            lock = self.lock()
        except BlockingIOError:
            # Another profile may still be checking idle time, before its first journal write.
            return {"state": "checking"}
        with lock:
            previous = self.state()
            if previous.get("phase") in ACTIVE or previous.get("phase") == "recoveryFailed":
                if manual or previous["phase"] in ACTIVE:
                    self.save(previous, "restoring")
                    self.launch(lock, recover=True)
                    return {**previous["view"], "state": "restoring"}
                return {**previous["view"], "state": "interrupted"}
            view, svc, keg = self.inspect()
            if view["state"] == "current" and previous.get("phase") == "failed" and previous.get("target") == self.target:
                if not manual:
                    return {**view, "state": "failed"}
                self.verify_install(self.target)
                self.save(previous, "done")
                return view
            if view["state"] != "available":
                return view
            if not manual and previous.get("target") == self.target and previous.get("phase") in {"failed", "restored"}:
                return {**view, "state": previous["phase"]}
            if svc["running"] and not self.maintenance():
                return {**view, "state": "waiting"}
            directory = tempfile.mkdtemp(prefix="upgrade-", dir=self.work)
            job = dict(target=self.target, view=view, backup=directory, pid=svc.get("pid"),
                       start=("start" if svc["registered"] else "run") if svc["running"] else "")
            try:
                self.prepare(svc, keg, job)
                self.save(job, "stopping" if job["start"] else "installing")
                self.launch(lock)
            except Exception:
                self.save(job, "failed")
                if job["start"] and self.listener(svc):
                    token = (self.data / ".control-token").read_text("ascii").strip()
                    request("DELETE", token)
                return {**view, "state": "failed"}
            return {**view, "state": job["phase"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["status", "refresh", "auto", "manual", "worker", "recover"])
    parser.add_argument("prefix", choices=["/opt/homebrew", "/usr/local"])
    parser.add_argument("target", type=str)
    parser.add_argument("fd", type=int, nargs="?")
    args = parser.parse_args()
    if not VERSION.fullmatch(args.target):
        raise ValueError("Invalid release version")
    backend = Backend(Path(args.prefix), args.target)
    if args.action in {"worker", "recover"}:
        with os.fdopen(args.fd, "a+"):
            backend.worker_fd = args.fd
            job = backend.state()
            if args.action == "worker":
                backend.perform(job)
            else:
                backend.recover(job)
        return
    if args.action == "refresh":
        view = backend.status()
        if view["state"] == "unpublished":
            # Refresh metadata only on an explicit check or a scheduled retry for a missing release.
            try:
                backend.brew("update", timeout=300)
            except (subprocess.SubprocessError, OSError):
                return {**view, "state": "network"}
        return backend.status()
    if args.action == "status":
        return backend.status()
    return backend.upgrade(args.action == "manual")


if __name__ == "__main__":
    os.umask(0o077)
    try:
        result = main()
    except (Unavailable, FileNotFoundError):
        result = {"state": "unavailable"}
    except Exception:
        result = {"state": "failed"}
    if result is not None:
        print(json.dumps(result))
