import contextlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from dev import Development, DevError, MacOS, Windows, MODEL_KEYS, PREFIX, read_prefs, update_prefs, write_json, windows_sid


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
        if sys.platform != "win32":
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        else:
            acl = subprocess.run(["icacls", str(target)], capture_output=True, text=True, check=True).stdout
            self.assertNotIn("(I)", acl)  # No inherited broad access to credentials.
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
        if sys.platform == "win32":
            subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                            "$null=New-Item -ItemType Junction -Path $env:DEV_TEST_LINK -Target $env:DEV_TEST_TARGET"],
                           env={**os.environ, "DEV_TEST_LINK": str(self.dev.paths()["library"]),
                                "DEV_TEST_TARGET": str(self.source)}, check=True, capture_output=True)
        else:
            self.dev.paths()["library"].symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(DevError):
            self.dev.validate_paths(self.dev.config())
        if sys.platform == "win32":
            self.dev.paths()["library"].rmdir()  # Remove the link itself, never recurse.
        else:
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

    def test_windows_pid_reuse_and_changed_arguments_are_not_stopped(self):
        adapter = Windows()
        record = {"pid": 42, "started": 1, "exe": "python.exe", "argv": ["python.exe", "dev.py"]}
        for changed in ({**record, "started": 2}, {**record, "exe": "other.exe"},
                        {**record, "argv": ["python.exe", "formal.py"]}):
            psutil = Mock()
            with patch.object(adapter, "processes", return_value={42: changed}), patch.dict(sys.modules, {"psutil": psutil}):
                adapter.stop(record)
                psutil.Process.assert_not_called()

    def test_windows_tasklist_localized_bytes_do_not_depend_on_python_utf8_mode(self):
        adapter = Windows()
        with patch("dev.subprocess.run", return_value=Mock(stdout="信息: 没有运行的任务匹配指定标准。\r\n".encode("gbk"))) as run:
            adapter.require_closed()
            self.assertNotIn("text", run.call_args.kwargs)
        with patch("dev.subprocess.run", return_value=Mock(stdout=b'"Zotero.exe","123","Console","1","123 K"\r\n')):
            with self.assertRaises(DevError):
                adapter.require_closed()

    def test_windows_sid_ignores_username_encoding_and_rejects_invalid_output(self):
        windows_sid.cache_clear()
        self.addCleanup(windows_sid.cache_clear)
        output = '"电脑\\用户","S-1-5-21-123-456-789-1001"\r\n'.encode("gbk")
        with patch("dev.subprocess.run", return_value=Mock(stdout=output)) as run:
            self.assertEqual(windows_sid(), "S-1-5-21-123-456-789-1001")
            self.assertNotIn("text", run.call_args.kwargs)
        windows_sid.cache_clear()
        with patch("dev.subprocess.run", return_value=Mock(stdout=b"unavailable")):
            with self.assertRaises(DevError):
                windows_sid()

    def test_stale_server_with_live_listener_blocks_cleanup(self):
        self.platform.owned.return_value = False
        self.platform.listeners.return_value = {"999"}
        with self.assertRaises(DevError):
            self.dev.stop_state({"server": {"pid": 42}, "watcher": {"pid": 43}})
        self.platform.stop.assert_not_called()

    def test_windows_launcher_adopts_only_matching_child(self):
        self.dev.platform = Windows()
        parent = {"pid": 42, "argv": ["venv/python.exe", "launcher.py", "--port", "8891"]}
        child = {"pid": 43, "parent": 42, "exe": str(self.dev.runtime / "python/python.exe"),
                 "argv": ["base/python.exe", *parent["argv"][1:]]}
        with patch.object(self.dev.platform, "owned", return_value=True), \
                patch.object(self.dev.platform, "listeners", return_value={"43"}), \
                patch.object(self.dev.platform, "processes", return_value={43: child}):
            state = {"server": parent}
            self.dev.adopt_server_listener(state)
            self.assertEqual(state, {"server": child, "launcher": parent})
            for bad in ({**child, "parent": 99}, {**child, "exe": str(self.source / "python.exe")},
                        {**child, "argv": ["python.exe", "formal.py"]}):
                with patch.object(self.dev.platform, "processes", return_value={43: bad}):
                    with self.assertRaises(DevError):
                        self.dev.adopt_server_listener({"server": parent})

    def test_windows_zotero_matching_uses_arguments_not_substrings(self):
        self.dev.platform = Windows()
        exe = str(self.base / "Program Files/Zotero/zotero.exe")
        write_json(self.dev.config_path, {"sourceProfile": str(self.source), "zoteroBin": exe})
        args = [exe, "-profile", str(self.dev.paths()["profile"]), "--dataDir", str(self.dev.paths()["library"])]
        good = {"exe": exe, "argv": args}
        bad = {"exe": exe, "argv": [*args[:2], str(self.source), *args[3:]]}
        with patch.object(self.dev.platform, "zotero", return_value=[good, bad]):
            self.assertEqual(self.dev.development_zotero(), [good])

    def prepare_switch(self, current="formal"):
        # Mock every OS operation: these tests must never close the user's Zotero.
        self.dev.platform = Mock(spec=Windows)
        self.dev.platform.owned.side_effect = lambda record: record is not None
        self.dev.platform.source_profile.return_value = self.source
        exe = self.base / "Program Files/Zotero/zotero.exe"
        exe.parent.mkdir(parents=True)
        exe.touch()
        write_json(self.dev.config_path, {"sourceProfile": str(self.source), "zoteroBin": str(exe)})
        formal = {"pid": 41, "started": 1, "exe": str(exe), "argv": [str(exe)]}
        development = {"pid": 42, "started": 1, "exe": str(exe), "argv": [str(exe),
                       "-profile", str(self.dev.paths()["profile"]), "--dataDir", str(self.dev.paths()["library"])]}
        selected = {"formal": [formal], "dev": [development], None: []}[current]
        self.dev.platform.zotero.return_value = selected
        write_json(self.dev.state_path, {"zotero": development} if current == "dev" else {})
        return formal, development

    def test_switch_formal_to_dev_checks_port_before_normal_close_and_start(self):
        formal, _ = self.prepare_switch()
        operations = Mock()
        operations.attach_mock(self.dev.platform.close_zotero, "close")
        with patch.object(self.dev, "port_free") as port, patch.object(self.dev, "start") as start:
            operations.attach_mock(port, "port")
            operations.attach_mock(start, "start")
            self.dev.switch()
        self.assertEqual([c[0] for c in operations.mock_calls], ["port", "close", "start"])
        self.dev.platform.close_zotero.assert_called_once_with(formal)
        self.dev.platform.stop.assert_not_called()

    def test_switch_dev_to_formal_stops_before_launch(self):
        _, development = self.prepare_switch("dev")
        operations = Mock()
        with patch.object(self.dev, "stop_state") as stop, patch.object(self.dev, "launch_formal") as launch:
            operations.attach_mock(stop, "stop")
            operations.attach_mock(launch, "launch")
            self.dev.switch()
            stop.assert_called_once_with({"zotero": development})
        self.assertEqual([c[0] for c in operations.mock_calls], ["stop", "launch"])
        self.dev.platform.close_zotero.assert_not_called()

    def test_switch_explicit_target_is_idempotent(self):
        self.prepare_switch("dev")
        with patch.object(self.dev, "stop_state") as stop, patch.object(self.dev, "start") as start:
            self.dev.switch("dev")
            stop.assert_not_called()
            start.assert_not_called()
        self.dev.platform.zotero.return_value = []
        write_json(self.dev.state_path, {})
        with patch.object(self.dev, "launch_formal") as launch:
            self.dev.switch("formal")
            launch.assert_called_once()

    def test_switch_formal_target_does_not_restart_formal(self):
        self.prepare_switch()
        with patch.object(self.dev, "launch_formal") as launch:
            self.dev.switch("formal")
            launch.assert_not_called()
        self.dev.platform.close_zotero.assert_not_called()

    def test_switch_defaults_to_dev_when_closed_and_formal_when_dev_server_remains(self):
        self.prepare_switch(None)
        with patch.object(self.dev, "start") as start, patch.object(self.dev, "port_free"):
            self.dev.switch()
            start.assert_called_once()
        write_json(self.dev.state_path, {"server": {"pid": 99}})
        with patch.object(self.dev, "require_idle"), patch.object(self.dev, "launch_formal") as launch:
            self.dev.switch()
            launch.assert_called_once()
        self.dev.platform.stop.assert_called_once_with({"pid": 99})

    def test_switch_busy_tasks_prevent_closing_either_environment(self):
        formal, development = self.prepare_switch()
        for current in (formal, development):
            self.dev.platform.zotero.return_value = [current]
            state = {"server": {"pid": 99}}
            if current == development:
                state["zotero"] = development
            write_json(self.dev.state_path, state)
            with patch.object(self.dev, "check_server"), \
                    patch.object(self.dev, "request", return_value={"tasks": [{"status": "running"}]}), \
                    patch.object(self.dev, "start") as start, patch.object(self.dev, "launch_formal") as launch:
                with self.assertRaisesRegex(DevError, "仍有开发翻译任务"):
                    self.dev.switch()
                start.assert_not_called()
                launch.assert_not_called()
        self.dev.platform.stop.assert_not_called()
        self.dev.platform.close_zotero.assert_not_called()

    def test_switch_exit_failure_or_busy_port_does_not_start_dev(self):
        self.prepare_switch()
        with patch.object(self.dev, "start") as start:
            with patch.object(self.dev, "port_free", side_effect=DevError("busy")):
                with self.assertRaisesRegex(DevError, "busy"):
                    self.dev.switch()
            self.dev.platform.close_zotero.assert_not_called()
            self.dev.platform.close_zotero.side_effect = DevError("still open")
            with patch.object(self.dev, "port_free"):
                with self.assertRaisesRegex(DevError, "still open"):
                    self.dev.switch()
            start.assert_not_called()

    def test_switch_rejects_unknown_profile_multiple_instances_and_untracked_dev(self):
        formal, development = self.prepare_switch()
        unknown = {**formal, "argv": [formal["exe"], "-profile", str(self.base / "another-profile")]}
        for instances in ([unknown], [formal, development], [development]):
            self.dev.platform.zotero.return_value = instances
            with patch.object(self.dev, "stop_state") as stop:
                with self.assertRaises(DevError):
                    self.dev.switch()
                stop.assert_not_called()
        self.dev.platform.close_zotero.assert_not_called()

    def test_switch_rejects_formal_process_in_dev_state(self):
        formal, _ = self.prepare_switch()
        write_json(self.dev.state_path, {"zotero": formal})
        with patch.object(self.dev, "stop_state") as stop:
            with self.assertRaisesRegex(DevError, "记录与实际 Profile 不匹配"):
                self.dev.switch()
            stop.assert_not_called()

    def test_formal_matching_checks_explicit_or_default_profile(self):
        formal, _ = self.prepare_switch()
        for args in ([], ["-profile", str(self.source)], ["--profile=" + str(self.source)]):
            self.assertTrue(self.dev.is_formal_zotero({**formal, "argv": [formal["exe"], *args]}))
        for args in (["-P", "another"], ["-profile"], ["-profile="],
                     ["-profile", str(self.source), "-profile", str(self.source)],
                     ["--dataDir", str(self.base / "other")]):
            self.assertFalse(self.dev.is_formal_zotero({**formal, "argv": [formal["exe"], *args]}))
        self.dev.platform.source_profile.return_value = self.base / "other-profile"
        self.assertFalse(self.dev.is_formal_zotero(formal))

    def test_switch_ignores_zotero_content_child(self):
        formal, _ = self.prepare_switch()
        self.dev.platform.zotero.return_value = [formal, {**formal, "pid": 88, "argv": [formal["exe"], "-contentproc"]}]
        with patch.object(self.dev, "launch_formal") as launch:
            self.dev.switch("formal")
            launch.assert_not_called()

    def test_launch_formal_uses_source_profile_and_does_not_inherit_dev_hook(self):
        formal, _ = self.prepare_switch()
        before = (self.source / "prefs.js").read_bytes()
        process = Mock(pid=formal["pid"])
        process.poll.return_value = None
        with patch.dict(os.environ, {"PDF2ZH_DEV_RUNTIME": str(self.dev.runtime)}), \
                patch("dev.subprocess.Popen", return_value=process) as launch, \
                patch("dev.subprocess.CREATE_NO_WINDOW", 0, create=True):
            self.dev.launch_formal()
            self.assertIn("PDF2ZH_DEV_RUNTIME", os.environ)
        command = launch.call_args.args[0]
        self.assertEqual(command, [formal["exe"], "-no-remote", "-profile", str(self.source)])
        self.assertNotIn("PDF2ZH_DEV_RUNTIME", launch.call_args.kwargs["env"])
        self.assertEqual((self.source / "prefs.js").read_bytes(), before)
        self.assertEqual(self.dev.state(), {})  # Formal process is never registered for dev stop.

    def test_normal_close_rejects_reused_pid_before_window_messages(self):
        adapter = Windows()
        with patch.object(adapter, "owned", return_value=False):
            with self.assertRaisesRegex(DevError, "身份已变化"):
                adapter.close_zotero({"pid": 42})

    @unittest.skipUnless(sys.platform == "win32", "Windows window messages (mocked)")
    def test_normal_close_only_messages_matching_main_windows(self):
        adapter = Windows()
        api = Mock()
        # Main window, another process, and a Zotero dialog.
        api.EnumWindows.side_effect = lambda callback, _: all(callback(hwnd, 0) for hwnd in (101, 102, 103))
        api.GetWindowThreadProcessId.side_effect = lambda hwnd, pointer: setattr(pointer._obj, "value", 99 if hwnd == 102 else 42)
        api.GetClassNameW.side_effect = lambda hwnd, buffer, _: setattr(buffer, "value", "MozillaDialogClass" if hwnd == 103 else "MozillaWindowClass")
        api.IsWindowVisible.return_value = True
        api.GetWindow.return_value = 0
        api.PostMessageW.return_value = True
        with patch("ctypes.WinDLL", return_value=api), patch.object(adapter, "owned", side_effect=[True, True, False]), \
                patch.object(adapter, "stop") as stop:
            adapter.close_zotero({"pid": 42})
            api.PostMessageW.assert_called_once_with(101, 0x0010, 0, 0)
            stop.assert_not_called()

    @unittest.skipUnless(sys.platform == "win32", "Windows window messages (mocked)")
    def test_normal_close_timeout_never_terminates_process(self):
        adapter = Windows()
        api = Mock()
        api.EnumWindows.side_effect = lambda callback, _: callback(101, 0)
        api.GetWindowThreadProcessId.side_effect = lambda hwnd, pointer: setattr(pointer._obj, "value", 42)
        api.GetClassNameW.side_effect = lambda hwnd, buffer, _: setattr(buffer, "value", "MozillaWindowClass")
        api.IsWindowVisible.return_value = True
        api.GetWindow.return_value = 0
        api.PostMessageW.return_value = True
        with patch("ctypes.WinDLL", return_value=api), patch.object(adapter, "owned", return_value=True), \
                patch("dev.time.sleep"), patch.object(adapter, "stop") as stop:
            with self.assertRaisesRegex(DevError, "尚未退出"):
                adapter.close_zotero({"pid": 42})
            stop.assert_not_called()

    @unittest.skipUnless(sys.platform == "win32", "Windows window messages (mocked)")
    def test_normal_close_without_main_window_stops_without_sending_messages(self):
        adapter = Windows()
        api = Mock()
        api.EnumWindows.return_value = True
        with patch("ctypes.WinDLL", return_value=api), patch.object(adapter, "owned", return_value=True):
            with self.assertRaisesRegex(DevError, "主窗口"):
                adapter.close_zotero({"pid": 42})
            api.PostMessageW.assert_not_called()

    @unittest.skipUnless(sys.platform == "win32", "Windows native lock")
    def test_windows_lock_rejects_concurrent_commands_then_releases(self):
        path = self.dev.runtime / "command.lock"
        with Windows.lock(path):
            with self.assertRaises(DevError):
                with Windows.lock(path):
                    self.fail("second lock acquired")
        with Windows.lock(path):
            pass

    @unittest.skipUnless(sys.platform == "win32", "Windows exclusive bind")
    def test_real_busy_port_is_not_reused(self):
        with socket.socket() as listener:
            try:
                listener.bind(("127.0.0.1", 8891))
            except OSError:
                self.skipTest("8891 already occupied; never interrupt its owner")
            listener.listen()
            with self.assertRaises(DevError):
                self.dev.port_free()

    @unittest.skipUnless(sys.platform == "win32", "Windows environment")
    def test_windows_environment_is_local_and_does_not_mutate_parent(self):
        before = dict(os.environ)
        env = self.dev.env()
        self.assertEqual(dict(os.environ), before)
        for key in ("UV_PROJECT_ENVIRONMENT", "UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR",
                    "UV_TOOL_DIR", "UV_TOOL_BIN_DIR"):
            self.assertTrue(Path(env[key]).is_relative_to(self.dev.runtime))
        self.assertEqual(env["ZOTERO_PLUGIN_KILL_COMMAND"], "exit /b 0")

    def test_windows_profile_discovery_handles_relative_and_ambiguous_profiles(self):
        base = self.base / "App Data/Zotero/Zotero"
        base.mkdir(parents=True)
        config = base / "profiles.ini"
        config.write_text("[Profile0]\nPath=Profiles/测试 default\nIsRelative=1\nDefault=1\n", encoding="utf-8")
        with patch.dict(os.environ, {"APPDATA": str(self.base / "App Data")}):
            self.assertEqual(Windows.source_profile(), (base / "Profiles/测试 default").resolve())
            config.write_text("[Profile0]\nPath=one\n[Profile1]\nPath=two\n", encoding="utf-8")
            with self.assertRaises(DevError):
                Windows.source_profile()

    @unittest.skipUnless(sys.platform == "win32", "Windows uv version alias")
    def test_uv_alias_is_allowed_only_inside_private_python_directory(self):
        folder = self.dev.runtime / "python"
        target = folder / "cpython-3.13.15-windows-x86_64-none"
        target.mkdir(parents=True)
        alias = folder / "cpython-3.13-windows-x86_64-none"
        for destination, accepted in ((target, True), (self.source, False)):
            subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                            "$null=New-Item -ItemType Junction -Path $env:DEV_TEST_LINK -Target $env:DEV_TEST_TARGET"],
                           env={**os.environ, "DEV_TEST_LINK": str(alias), "DEV_TEST_TARGET": str(destination)},
                           check=True, capture_output=True)
            try:
                if accepted:
                    self.dev.validate_paths(self.dev.config())
                else:
                    with self.assertRaises(DevError):
                        self.dev.validate_paths(self.dev.config())
            finally:
                alias.rmdir()

    @unittest.skipUnless(sys.platform == "win32", "Windows credential ACL")
    def test_acl_failure_does_not_replace_or_write_credentials(self):
        target = self.dev.paths()["profile"] / "prefs.js"
        target.write_text("original", encoding="utf-8")
        with patch("dev.windows_private", side_effect=OSError("ACL failed")):
            with self.assertRaises(OSError):
                update_prefs(target, {PREFIX + "llmApis": "SECRET"})
        self.assertEqual(target.read_text(encoding="utf-8"), "original")
        self.assertNotIn("SECRET", target.with_name("prefs.js.tmp").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
