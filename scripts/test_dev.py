import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from dev import Development, DevError, MacOS, MODEL_KEYS, PREFIX, read_prefs, update_prefs, write_json


class DevelopmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.source = self.base / "formal"
        self.source.mkdir()
        self.platform = Mock()
        self.platform.owned.return_value = False
        self.dev = Development(self.base / "repo", self.platform)
        for path in self.dev.paths().values():
            path.mkdir(parents=True)
        write_json(self.dev.config_path, {"sourceProfile": str(self.source), "zoteroBin": "/Applications/Zotero.app/Contents/MacOS/zotero"})
        self.models = [{"key": "model-1", "service": "openai", "apiKey": "SECRET",
                        "apiUrl": "https://example.test/v1", "model": "example",
                        "apiProtocol": "responses", "requestOptions": {"temperature": 0.2},
                        "extraData": {"deployment": "sample"}}]
        update_prefs(self.source / "prefs.js", {
            PREFIX + "llmApis": json.dumps(self.models), PREFIX + "selectedApiKey": "model-1",
            PREFIX + "profileSchemaVersion": 1, PREFIX + "new_serverip": "http://127.0.0.1:8890",
            "extensions.zotero.dataDir": str(self.base / "formal-library"), "extensions.zotero.useDataDir": True,
        })

    def test_copy_is_complete_private_and_independent(self):
        before = (self.source / "prefs.js").read_bytes()
        target = self.dev.paths()["profile"] / "prefs.js"
        update_prefs(target, {PREFIX + "taskBindings": "keep", PREFIX + "service": "old"})
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.dev.sync_models()
        copied = read_prefs(target)
        self.assertEqual(json.loads(copied[PREFIX + "llmApis"]), self.models)
        self.assertEqual(copied[PREFIX + "selectedApiKey"], "model-1")
        self.assertNotIn(PREFIX + "service", copied)
        self.assertEqual(copied[PREFIX + "taskBindings"], "keep")
        self.assertNotIn("SECRET", output.getvalue())
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertTrue(list(self.dev.paths()["backups"].glob("*.js")))
        user = read_prefs(target.with_name("user.js"))
        self.assertEqual(user[PREFIX + "new_serverip"], self.dev.url)
        self.assertFalse(MODEL_KEYS.intersection(user))
        update_prefs(target, {PREFIX + "selectedApiKey": "changed"})
        self.assertEqual((self.source / "prefs.js").read_bytes(), before)

    def test_source_user_overrides_are_copied(self):
        update_prefs(self.source / "user.js", {PREFIX + "selectedApiKey": "override"})
        self.dev.sync_models()
        self.assertEqual(read_prefs(self.dev.paths()["profile"] / "prefs.js")[PREFIX + "selectedApiKey"], "override")

    def test_running_zotero_blocks_sync_without_writes(self):
        self.platform.require_closed.side_effect = DevError("running")
        with self.assertRaises(DevError):
            self.dev.sync_models()
        self.assertFalse((self.dev.paths()["profile"] / "prefs.js").exists())

    def test_invalid_models_do_not_replace_developer_config(self):
        target = self.dev.paths()["profile"] / "prefs.js"
        target.write_text("// keep\n")
        update_prefs(self.source / "prefs.js", {PREFIX + "llmApis": "bad SECRET"})
        with self.assertRaises(DevError) as caught:
            self.dev.sync_models()
        self.assertNotIn("SECRET", str(caught.exception))
        self.assertEqual(target.read_text(), "// keep\n")

    def test_symlink_and_formal_library_alias_are_rejected(self):
        self.dev.paths()["library"].rmdir()
        self.dev.paths()["library"].symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(DevError):
            self.dev.validate_paths(self.dev.config())
        self.dev.paths()["library"].unlink()
        update_prefs(self.source / "prefs.js", {"extensions.zotero.dataDir": str(self.dev.runtime)})
        with self.assertRaises(DevError):
            self.dev.validate_paths(self.dev.config())

    def test_initializer_keeps_existing_models_and_data(self):
        self.dev.sync_models()
        target = self.dev.paths()["profile"] / "prefs.js"
        update_prefs(target, {PREFIX + "selectedApiKey": "dev-choice"})
        document = self.dev.paths()["library"] / "existing.pdf"
        document.write_bytes(b"keep")
        with patch("dev.shutil.which", return_value="tool"), patch("dev.subprocess.run"), patch("dev.Path.is_file", return_value=True):
            self.dev.init()
        self.assertEqual(read_prefs(target)[PREFIX + "selectedApiKey"], "dev-choice")
        self.assertEqual(document.read_bytes(), b"keep")

    def test_active_tasks_prevent_all_stops(self):
        self.platform.owned.return_value = True
        for status in ("queued", "running", "cancelling"):
            with patch.object(self.dev, "check_server"), patch.object(self.dev, "request", return_value={"tasks": [{"status": status}]}):
                with self.assertRaises(DevError):
                    self.dev.stop_state({"server": {"pid": 9}, "zotero": {"pid": 10}})
        self.platform.stop.assert_not_called()

    def test_failed_start_cleans_only_new_processes(self):
        self.platform.owned.return_value = False
        new = {"pid": 912}
        def fail(state):
            state["server"] = new
            raise DevError("health failed")
        with patch.object(self.dev, "port_free"), patch.object(self.dev, "start_server", side_effect=fail), patch.object(self.dev, "development_zotero", return_value=[]):
            with self.assertRaises(DevError):
                self.dev.start()
        self.platform.stop.assert_called_once_with(new)
        self.assertEqual(self.dev.state(), {})

    def test_busy_port_prevents_launch(self):
        with patch.object(self.dev, "port_free", side_effect=DevError("busy")), patch.object(self.dev, "launch") as launch:
            with self.assertRaises(DevError):
                self.dev.start()
            launch.assert_not_called()

    def test_reused_pid_is_not_signalled(self):
        adapter = MacOS()
        record = {"pid": 42, "started": "old", "command": "developer"}
        with patch.object(adapter, "processes", return_value={42: {"pid": 42, "started": "new", "command": "formal"}}), patch("dev.os.kill") as kill:
            adapter.stop(record)
            kill.assert_not_called()

    def test_macos_process_query_preserves_chinese_paths(self):
        output = "42 1 Thu Oct  1 12:00:00 2026 /Applications/Zotero.app/Contents/MacOS/zotero -profile /开发/profile\n"
        with patch("dev.subprocess.run", return_value=Mock(stdout=output)) as run:
            process = MacOS.processes()[42]
        self.assertIn("/开发/profile", process["command"])
        self.assertEqual(run.call_args.kwargs["env"]["LC_ALL"], "en_US.UTF-8")

    def test_listener_ownership_is_checked(self):
        self.platform.owned.return_value = True
        self.platform.listeners.return_value = {"999"}
        with patch.object(self.dev, "request", return_value={"workspace": {"path": str(self.dev.paths()["tasks"])}}):
            with self.assertRaisesRegex(DevError, "PID"):
                self.dev.check_server({"server": {"pid": 42}})

    def test_graceful_stop_uses_plugin_request_not_process_signal(self):
        self.platform.owned.side_effect = [True, False]
        self.dev.stop_state({"zotero": {"pid": 42}})
        self.platform.stop.assert_not_called()
        self.assertFalse((self.dev.runtime / "quit-request.json").exists())

    def test_closed_source_cannot_erase_existing_models(self):
        self.dev.sync_models()
        target = self.dev.paths()["profile"] / "prefs.js"
        before = target.read_bytes()
        (self.source / "prefs.js").unlink()
        with self.assertRaises(DevError):
            self.dev.sync_models()
        self.assertEqual(target.read_bytes(), before)

    def test_parser_does_not_execute_javascript(self):
        path = self.base / "prefs.js"
        path.write_text('user_pref("x", process.exit());\n')
        with self.assertRaises(DevError):
            read_prefs(path)


if __name__ == "__main__":
    unittest.main()
