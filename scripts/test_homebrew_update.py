"""Homebrew upgrade transactions using synthetic kegs, service state and task files."""
import json
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

if sys.platform == "win32":
    raise unittest.SkipTest("Homebrew uses POSIX process locks")

import homebrew_update as updater


class Fixture(updater.Backend):
    def __init__(self, prefix):
        super().__init__(prefix.resolve(), "1.9.8")
        self.stable = self.target
        self.versions = []
        self.pinned = False
        self.running = "1.9.6"
        self.registered = True
        self.held = self.busy = False
        self.fail = ""
        self.calls = []
        self.data.mkdir(parents=True)
        (self.data / "tasks.json").write_text("original")
        self.opt.parent.mkdir()
        self.install(self.running)
        self.plist = prefix / "service.plist"
        self.plist.write_bytes(plistlib.dumps({"Label": "sh.brew." + updater.NAME,
            "ProgramArguments": [str(self.opt / "bin" / updater.NAME), "--port", "8890"]}))

    def install(self, version):
        keg = self.rack / version
        (keg / "libexec/venv/bin").mkdir(parents=True)
        (keg / "libexec/venv/bin" / updater.NAME).touch()
        exe = keg / "libexec/venv/bin/python"
        exe.write_text("#!/bin/sh\necho " + version + "\n")
        exe.chmod(0o700)
        (keg / "INSTALL_RECEIPT.json").write_text(json.dumps({"source":{"tap":updater.TAP}}))
        self.opt.unlink(missing_ok=True)
        self.opt.symlink_to(keg)
        self.versions.append({"version":version})

    def brew(self, *args, timeout=120):
        self.calls.append(args)
        if args[0] == "info":
            return {"formulae":[dict(tap=updater.TAP, name=updater.NAME, installed=self.versions,
                                     versions={"stable":self.stable}, pinned=self.pinned)]}
        if args[:2] == ("services", "info"):
            return [dict(name=updater.NAME, service_name="sh.brew." + updater.NAME, running=bool(self.running),
                         loaded=bool(self.running), registered=self.registered, pid=12 if self.running else None,
                         loaded_file=str(self.plist), file=str(self.plist))]
        if args[:2] == ("services", "stop"):
            self.running = None
            self.held = False
        elif args[0] == "upgrade":
            if self.fail == "upgrade":
                raise subprocess.CalledProcessError(1, args)
            self.install(self.stable)
        elif args[0] == "services" and args[1] in {"start", "run"}:
            self.plist = Path(args[3].removeprefix("--file="))
            executable = Path(plistlib.loads(self.plist.read_bytes())["ProgramArguments"][0])
            self.running = self.opt.resolve().name if executable == self.opt / "bin" / updater.NAME else executable.parents[3].name
            self.registered = args[1] == "start"
            if self.running == self.target and self.fail == "health":
                (self.data / "tasks.json").write_text("new schema")
        else:
            raise AssertionError(args)

    def health(self, svc):
        return {"version":self.running, "maintenance":{"held":self.held}}
    def listener(self, svc):
        return bool(svc["running"])
    def maintenance(self, idle=60):
        if self.busy:
            return False
        self.held = True
        return True
    def wait_health(self, version):
        if self.running != version or (version == self.target and self.fail == "health"):
            raise updater.Unavailable()
    def launch(self, lock, recover=False):
        # Enqueue without running inline, like the detached production worker.
        self.launch_phase = "recover" if recover else "worker"


class HomebrewUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.b = Fixture(Path(self.temp.name))
        self.enterContext(patch.object(updater.platform, "machine", return_value="arm64"))
        self.port = self.enterContext(patch.object(updater, "port_open", return_value=False))

    def run_worker(self, manual=False):
        response = self.b.upgrade(manual)
        self.assertIn(response["state"], {"stopping","installing"})
        self.b.perform(self.b.state())

    def test_upgrade_preserves_tasks_and_login_registration(self):
        for registered in (True, False):
            with self.subTest(registered=registered), tempfile.TemporaryDirectory() as tmp:
                self.b = Fixture(Path(tmp))
                self.b.registered = registered
                self.run_worker()
                self.assertEqual(self.b.status()["state"], "current")
                self.assertEqual(self.b.running, self.b.target)
                self.assertEqual(self.b.registered, registered)
                self.assertTrue(self.b.plist.exists() if not registered else self.b.registered)
                self.assertEqual((self.b.data/"tasks.json").read_text(), "original")
                self.assertEqual(self.b.calls.count(("upgrade", updater.FORMULA)), 1)

    def test_wait_and_duplicate_admission(self):
        self.b.busy = True
        self.assertEqual(self.b.upgrade(False)["state"], "waiting")
        self.assertFalse(self.b.journal.exists())
        self.b.busy = False
        with self.b.lock():
            self.assertEqual(self.b.upgrade(True)["state"], "checking")
        self.assertEqual(self.b.upgrade(True)["state"], "stopping")
        self.assertEqual(self.b.status()["state"], "interrupted")
        with self.b.lock():
            self.assertEqual(self.b.status()["state"], "stopping")

    def test_stopped_install_stays_stopped(self):
        self.b.running = None
        self.run_worker()
        self.assertIsNone(self.b.running)
        self.assertEqual(self.b.status()["state"], "current")
        self.assertFalse(any(cmd[:2] in {("services","stop"),("services","start"),("services","run")} for cmd in self.b.calls))

    def test_recovery_recognizes_finished_install_after_interruption(self):
        for running in (True, False):
            with self.subTest(running=running), tempfile.TemporaryDirectory() as tmp:
                backend = Fixture(Path(tmp))
                if not running:
                    backend.running = None
                backend.upgrade(True)
                job = backend.state()
                backend.install(backend.target)
                if running:
                    job["startedNew"] = True
                    backend.running = backend.target
                backend.save(job, "verifying")
                backend.recover(job)
                self.assertEqual(backend.state()["phase"], "done")
                self.assertEqual(backend.status()["state"], "current")
                self.assertEqual(bool(backend.running), running)
                self.assertFalse(Path(job["backup"]).exists())

    def test_recovery_after_stop_before_backup_restarts_original_service(self):
        self.b.upgrade(True)
        job = self.b.state()
        self.b.stop()
        self.b.save(job, "backup")
        self.b.recover(job)
        self.assertEqual(self.b.state()["phase"], "restored")
        self.assertEqual(self.b.running, "1.9.6")
        self.assertEqual((self.b.data / "tasks.json").read_text(), "original")

    def test_status_rechecks_journal_after_worker_releases_lock(self):
        self.b.upgrade(True)
        stale = self.b.state()
        self.b.perform(self.b.state())
        with patch.object(self.b, "state", side_effect=[stale, self.b.state()]):
            self.assertEqual(self.b.status()["state"], "current")

    def test_detached_worker_keeps_lock_after_launcher_exits(self):
        prefix = self.b.prefix
        worker = prefix / "worker.py"
        worker.write_text('''import fcntl, os, sys
from pathlib import Path
prefix = Path(sys.argv[2])
with os.fdopen(int(sys.argv[4]), "a+"), (prefix / "barrier").open("a+") as barrier:
    (prefix / "ready").write_text(str(os.getsid(0) == os.getpid()))
    fcntl.flock(barrier, fcntl.LOCK_EX)
(prefix / "finished").touch()
''')
        launcher = '''import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import homebrew_update
backend = homebrew_update.Backend(Path(sys.argv[2]), '1.9.8')
homebrew_update.__file__ = sys.argv[3]
with backend.lock() as lock:
    backend.launch(lock)
'''
        def wait_for(name):
            path = prefix / name
            deadline = time.monotonic() + 5
            while not path.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(path.exists(), name)
            return path

        with (prefix / "barrier").open("a+") as barrier:
            updater.fcntl.flock(barrier, updater.fcntl.LOCK_EX)
            subprocess.run([sys.executable, "-I", "-c", launcher, Path(updater.__file__).parent,
                            prefix, worker], check=True, timeout=10)
            self.assertEqual(wait_for("ready").read_text(), "True")
            self.assertEqual(self.b.upgrade(True)["state"], "checking")
        wait_for("finished")
        with self.b.lock():
            self.assertFalse(self.b.journal.exists())

    def test_reject_changed_target_and_unowned_service(self):
        for stable, result in [("1.9.7","unpublished"),("1.9.9","ahead")]:
            self.b.stable = stable
            self.assertEqual(self.b.upgrade(False)["state"], result)
        self.b.stable = self.b.target
        self.b.pinned = True
        self.assertEqual(self.b.upgrade(True)["state"], "pinned")
        self.b.running = None
        self.port.return_value = True
        with self.assertRaises(updater.Unavailable):
            self.b.upgrade(True)
        self.port.return_value = False
        (self.b.opt/"INSTALL_RECEIPT.json").write_text('{"source":{"tap":"other/tap"}}')
        with self.assertRaises(updater.Unavailable):
            self.b.upgrade(True)
        self.assertFalse(self.b.journal.exists())

    def test_failed_new_runtime_restores_data_and_explicit_old_keg(self):
        self.b.fail = "health"
        self.enterContext(patch.object(updater, "request", return_value=(200, {"workspace":{"path":str(self.b.data)}})))
        self.run_worker()
        self.assertEqual(self.b.status()["state"], "restored")
        self.assertEqual(self.b.running, "1.9.6")
        self.assertEqual(self.b.opt.resolve().name, self.b.target)
        self.assertEqual((self.b.data/"tasks.json").read_text(), "original")
        self.assertEqual(self.b.upgrade(False)["state"], "restored")
        self.b.fail = ""
        self.run_worker(manual=True)
        self.assertEqual(self.b.running, self.b.target)
        self.assertEqual(self.b.calls.count(("upgrade", updater.FORMULA)), 1)

    def test_upgrade_error_resumes_old_process_and_blocks_auto_retry(self):
        self.b.fail = "upgrade"
        self.run_worker()
        self.assertEqual(self.b.running, "1.9.6")
        self.assertEqual(self.b.upgrade(False)["state"], "restored")

    def test_worker_checks_service_and_formula_again_before_installing(self):
        self.b.upgrade(True)
        self.b.stable = "1.9.9"
        self.b.perform(self.b.state())
        self.assertEqual(self.b.state()["phase"], "restored")
        self.assertNotIn(("upgrade", updater.FORMULA), self.b.calls)

    def test_does_not_stop_replacement_listener(self):
        self.b.upgrade(True)
        self.b.held = False
        self.b.perform(self.b.state())
        self.assertNotIn(("services", "stop", updater.FORMULA), self.b.calls)
        self.assertNotIn(("upgrade", updater.FORMULA), self.b.calls)

    def test_recovery_failed_remains_actionable_even_with_unhealthy_listener(self):
        self.b.upgrade(True)
        self.b.save(self.b.state(), "recoveryFailed")
        self.b.health = lambda svc: self.fail("must use the journal")
        self.assertEqual(self.b.status()["state"], "recoveryFailed")
        self.assertEqual(self.b.upgrade(True)["state"], "restoring")
