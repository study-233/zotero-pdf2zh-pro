"""Native macOS/Windows discovery tests; no accounts, network, or inference."""
import json
import gc
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch
from codex_client import CodexError, _read_version
from codex_discovery import (Candidate, CodexSelection, collect_candidates, discovery_request,
                             cancel_discovery, version_key)


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="Codex 中文 space ")))
        self.enterContext(patch.dict(os.environ, {"PATH": "", "LOCALAPPDATA": str(self.root / "Local"),
                                                "APPDATA": str(self.root / "Roaming")}, clear=True))
        self.enterContext(patch("codex_discovery.Path.home", return_value=self.root))
        self.enterContext(patch("codex_discovery._registered_mac_apps", return_value=[]))

    def file(self, relative, text="binary"):
        p = self.root / relative
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        p.chmod(0o755)
        return p

    def test_desktop_custom_and_all_path_entries_in_priority_order(self):
        desktop = self.file("Local/OpenAI/Codex/bin/hash/codex.exe")
        self.file("Local/OpenAI/Codex/bin/.staging/codex.exe")
        custom = self.file("custom/codex.exe")
        first, second = self.file("first/codex.exe"), self.file("second/codex.exe")
        with patch.dict(os.environ, {"PATH": f'{first.parent};{second.parent};{first.parent}', "CODEX_INSTALL_DIR": str(custom.parent)}):
            candidates = collect_candidates(system="win32")
        self.assertEqual([c.path for c in candidates], [str(p.resolve()) for p in [desktop, custom, first, second]])
        self.assertEqual([c.source for c in candidates], ["desktop", "install_dir", "path", "path"])

    def test_mac_user_and_registered_renamed_apps(self):
        user = self.file("Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex")
        renamed = self.file("Elsewhere/My Codex.app/Contents/Resources/codex")
        with patch("codex_discovery._registered_mac_apps", return_value=[self.root / "Elsewhere/My Codex.app"]):
            candidates = collect_candidates(system="darwin")
        self.assertTrue({str(user.resolve()), str(renamed.resolve())} <= {c.path for c in candidates if c.source == "desktop"})

    def test_explicit_missing_path_never_uses_desktop(self):
        self.file("Local/OpenAI/Codex/bin/version/codex.exe")
        candidates = collect_candidates(str(self.root / "missing.exe"), system="win32")
        self.assertEqual(len(candidates), 1)
        self.assertEqual((candidates[0].issue, candidates[0].source), ("codex_invalid_path", "manual"))

    def test_npm_old_new_and_hoisted_layouts_without_node(self):
        for system, arch, pkg, target in (("win32", "ARM64", "win32-arm64", "aarch64-pc-windows-msvc"),
                                         ("win32", "AMD64", "win32-x64", "x86_64-pc-windows-msvc"),
                                         ("darwin", "arm64", "darwin-arm64", "aarch64-apple-darwin"),
                                         ("darwin", "x86_64", "darwin-x64", "x86_64-apple-darwin")):
            for layout in ("old", "nested", "hoisted"):
                with self.subTest(system=system, arch=arch, layout=layout):
                    prefix = f"{pkg}/{layout}"
                    root = f"{prefix}/node_modules/@openai/codex"
                    self.file(root + "/package.json", json.dumps({"name": "@openai/codex"}))
                    entry = self.file(prefix + "/codex.cmd") if system == "win32" else self.file(root + "/bin/codex.js")
                    base = root if layout == "old" else root + f"/node_modules/@openai/codex-{pkg}" if layout == "nested" else f"{prefix}/node_modules/@openai/codex-{pkg}"
                    native = self.file(f"{base}/vendor/{target}/{'codex' if layout == 'old' else 'bin'}/{'codex.exe' if system == 'win32' else 'codex'}")
                    with patch("codex_discovery.platform.machine", return_value=arch), patch.dict(os.environ, {"PROCESSOR_ARCHITECTURE": arch}):
                        candidate = collect_candidates(str(entry), system=system)[0]
                    self.assertIsNone(candidate.issue)
                    self.assertEqual(candidate.path, str(native.resolve()))

    def test_unknown_windows_wrapper_is_never_executed(self):
        entry = self.file("untrusted/codex.cmd", "echo should not execute")
        with patch("codex_discovery.subprocess.Popen") as launch:
            self.assertEqual(collect_candidates(str(entry), system="win32")[0].issue, "codex_dependency_missing")
        launch.assert_not_called()

    def test_empty_environment_does_not_search_relative_npm_directory(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(collect_candidates(system="win32"), [])


class VersionTests(unittest.TestCase):
    def process(self, stdout, stderr="", code=0):
        process = Mock(returncode=code)
        process.communicate.return_value = (stdout, stderr)
        process.poll.return_value = code
        return process

    def test_full_prerelease_and_semver_order(self):
        with patch("codex_client.subprocess.Popen", return_value=self.process("codex-cli 0.162.0-alpha.17.2\n")):
            self.assertEqual(_read_version("/fake/codex"), "0.162.0-alpha.17.2")
        self.assertGreater(version_key("0.162.0-alpha.10"), version_key("0.162.0-alpha.2"))
        self.assertGreater(version_key("0.162.0"), version_key("0.162.0-alpha.10"))

    def test_errors_are_distinct_and_redacted(self):
        cases = [("", "env: node: No such file or directory SECRET", 127, "codex_dependency_missing"),
                 ("", "PRIVATE CONFIG", 1, "codex_start_failed"),
                 ("other-tool 99.0.0", "", 0, "codex_version_unrecognized"),
                 ("codex-cli 0.153.3", "", 0, "codex_version_too_old"),
                 ("codex-cli 0.153.4-alpha.1", "", 0, "codex_version_too_old")]
        for out, err, rc, expected in cases:
            with self.subTest(expected=expected), patch("codex_client.subprocess.Popen", return_value=self.process(out, err, rc)):
                with self.assertRaises(CodexError) as raised:
                    _read_version("/fake/codex")
                self.assertEqual(raised.exception.code, expected)
                self.assertNotIn("SECRET", str(raised.exception))
                self.assertNotIn("PRIVATE", str(raised.exception))

    def test_version_uses_same_environment_and_no_shell(self):
        env = {"PATH": "/system", "CODEX_HOME": "/existing"}
        with patch("codex_client.subprocess.Popen", return_value=self.process("codex-cli 0.160.0")) as launch:
            _read_version("path with spaces", env=env)
        self.assertEqual(launch.call_args.args[0], ["path with spaces", "--version"])
        self.assertEqual(launch.call_args.kwargs["env"], env)
        self.assertNotIn("shell", launch.call_args.kwargs)

    def test_cancel_real_version_child_reaps_process(self):
        real, children = subprocess.Popen, []
        def launch(*args, **kwargs):
            process = real([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
            children.append(process)
            return process
        check = Mock(side_effect=[None, CodexError("codex_cancelled", "cancelled")])
        with patch("codex_client.subprocess.Popen", side_effect=launch), self.assertRaises(CodexError):
            _read_version("ignored", check)
        self.assertIsNotNone(children[0].poll())
        self.assertTrue(children[0].stdout.closed and children[0].stderr.closed)


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.candidates = []
        for name, source in (("desktop-old", "desktop"), ("desktop-new", "desktop"), ("terminal", "path")):
            p = self.root / name
            p.write_text("binary")
            self.candidates.append(Candidate(str(p), source))
        self.enterContext(patch("codex_discovery.collect_candidates", side_effect=lambda *a, **kw: list(self.candidates)))
        versions = dict(zip([c.path for c in self.candidates], ["0.160.0", "0.162.0-alpha.10", "0.163.0"]))
        self.version = self.enterContext(patch("codex_client._read_version", side_effect=lambda p, *a, **kw: versions[p]))
        self.clients = []
        def build(path, **options):
            client = Mock(cli_path=path, version=None)
            client._transport = Mock(_failure=None)
            client.list_models.return_value = [{"id": "model"}]
            self.clients.append(client)
            return client
        self.factory = self.enterContext(patch("codex_client.CodexClient", side_effect=build))
        self.selection = CodexSelection()
        self.addCleanup(self.selection.close)

    def test_newest_desktop_preferred_even_over_newer_terminal(self):
        runtime = self.selection.discover()
        self.assertEqual(runtime["path"], self.candidates[1].path)
        self.assertEqual(runtime["version"], "0.162.0-alpha.10")
        self.assertEqual(self.factory.call_count, 1)
        self.clients[0].translate.assert_not_called()
        self.clients[0].check_ready.assert_not_called()

    def test_broken_desktops_fall_back_with_reasons(self):
        def versions(path, *args, **kw):
            if "desktop" in path:
                raise CodexError("codex_version_too_old", "old")
            return "0.160.0"
        self.version.side_effect = versions
        result = self.selection.discover()
        self.assertEqual(result["source"], "path")
        self.assertEqual(len(result["attempts"]), 2)

    def test_protocol_failure_closes_peer_and_falls_back(self):
        builder = self.factory.side_effect
        def build(path, **kw):
            c = builder(path, **kw)
            if path == self.candidates[1].path:
                c._ensure_started.side_effect = CodexError("codex_incompatible", "protocol")
            return c
        self.factory.side_effect = build
        self.assertEqual(self.selection.discover()["path"], self.candidates[0].path)
        self.clients[0].close.assert_called_once()

    def test_auth_network_quota_isolation_do_not_trigger_fallback(self):
        builder = self.factory.side_effect
        for code in ["codex_not_logged_in", "codex_request_failed", "codex_quota_exhausted", "codex_isolation_failed"]:
            with self.subTest(code=code):
                def build(path, **kw):
                    c = builder(path, **kw)
                    c._ensure_started.side_effect = CodexError(code, "fixed message")
                    return c
                self.factory.side_effect = build
                before = self.factory.call_count
                with self.assertRaises(CodexError) as raised:
                    self.selection.discover(force=True)
                self.assertEqual(raised.exception.code, code)
                self.assertEqual(self.factory.call_count - before, 1)

    def test_cache_force_file_environment_and_expiration(self):
        first = self.selection.discover()
        self.assertEqual(self.selection.discover(), first)
        self.assertEqual(self.factory.call_count, 1)
        self.selection.discover(force=True)
        self.assertEqual(self.factory.call_count, 2)
        Path(first["path"]).write_text("new binary")
        self.selection.discover()
        self.assertEqual(self.factory.call_count, 3)
        with patch.dict(os.environ, {"CODEX_HOME": "/different-user-state"}):
            self.selection.discover()
        self.assertEqual(self.factory.call_count, 4)
        self.selection._expires = 0
        self.selection.discover()
        self.assertEqual(self.factory.call_count, 5)

    def test_refresh_pins_inflight_translation_and_never_replays(self):
        self.selection.discover()
        first = self.clients[0]
        entered, release = threading.Event(), threading.Event()
        first.translate.side_effect = lambda *a, **kw: (entered.set(), release.wait(3), "translation")[-1]
        with ThreadPoolExecutor() as pool:
            job = pool.submit(self.selection.translate, "source", model="model")
            self.assertTrue(entered.wait(1))
            self.selection.discover(force=True)
            first.close.assert_not_called()
            release.set()
            self.assertEqual(job.result(2), "translation")
        first.close.assert_called_once()
        first.translate.assert_called_once()
        self.clients[-1].translate.assert_not_called()

    def test_whole_translator_session_pins_installation_after_app_update(self):
        session = self.selection.session()
        session.translate("first paragraph", model="model")
        old = self.clients[0]
        Path(session.runtime["path"]).unlink()
        self.candidates.pop(1)
        self.selection.discover(force=True)
        old.close.assert_not_called()
        session.translate("second paragraph", model="model")
        self.assertEqual(old.translate.call_count, 2)
        self.clients[-1].translate.assert_not_called()
        del session
        gc.collect()
        old.close.assert_called_once()

    def test_malformed_handshake_falls_back_without_exposing_payload(self):
        builder = self.factory.side_effect
        def build(path, **kw):
            client = builder(path, **kw)
            if path == self.candidates[1].path:
                client._ensure_started.side_effect = KeyError("PRIVATE CONFIG")
            return client
        self.factory.side_effect = build
        result = self.selection.discover()
        self.assertEqual(result["attempts"][0]["code"], "codex_protocol_error")
        self.assertNotIn("PRIVATE", str(result))

    def test_total_budget_stops_probing_more_candidates(self):
        # Keep the synthetic clock advanced after the failure, as a real clock is.
        now = time.monotonic()
        clock = [now]
        def version(path, check, **kw):
            clock[0] += 11
            check()
        self.version.side_effect = version
        with patch("codex_discovery.time.monotonic", side_effect=lambda: clock[0]):
            with self.assertRaises(CodexError) as raised:
                self.selection.discover()
        self.assertEqual(raised.exception.code, "codex_timeout")
        self.factory.assert_not_called()

    def test_concurrent_detection_shares_probe(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.selection.discover(), range(4)))
        self.assertEqual(self.factory.call_count, 1)
        self.assertTrue(all(r == results[0] for r in results))

    def test_cancel_stops_without_fallback(self):
        def cancelled():
            raise CodexError("codex_cancelled", "cancelled")
        with self.assertRaises(CodexError) as raised:
            self.selection.discover(check_cancelled=cancelled)
        self.assertEqual(raised.exception.code, "codex_cancelled")
        self.factory.assert_not_called()

    def test_explicit_failure_has_action_and_attempts(self):
        self.selection.cli_path = "explicit"
        self.candidates = [Candidate("/missing", "manual", "codex_invalid_path")]
        with self.assertRaises(CodexError) as raised:
            self.selection.discover()
        self.assertEqual(raised.exception.code, "codex_invalid_path")
        self.assertIn("清空", str(raised.exception))
        self.assertEqual(len(raised.exception.attempts), 1)
        self.factory.assert_not_called()

    def test_candidate_timeout_falls_back_within_total_budget(self):
        builder = self.factory.side_effect
        def build(path, **kw):
            c = builder(path, **kw)
            if path == self.candidates[1].path:
                def hang(check):
                    with patch("codex_discovery.time.monotonic", return_value=time.monotonic() + 6):
                        check()
                c._ensure_started.side_effect = hang
            return c
        self.factory.side_effect = build
        result = self.selection.discover()
        self.assertEqual(result["path"], self.candidates[0].path)
        self.assertEqual(result["attempts"][0]["code"], "codex_timeout")


class RequestCancellationTests(unittest.TestCase):
    def test_cancel_before_registration_and_during_probe(self):
        cancel_discovery("cancel-before-123")
        with self.assertRaises(CodexError) as raised:
            with discovery_request("cancel-before-123"):
                self.fail("cancelled request ran")
        self.assertEqual(raised.exception.code, "codex_cancelled")
        with discovery_request("cancel-during-123") as check:
            check()
            cancel_discovery("cancel-during-123")
            with self.assertRaises(CodexError):
                check()

    def test_invalid_ids_do_not_echo_input(self):
        with self.assertRaises(CodexError) as raised:
            cancel_discovery("PRIVATE INPUT")
        self.assertNotIn("PRIVATE", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
