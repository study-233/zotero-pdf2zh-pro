"""Upgrade persistence regressions, using synthetic installations and data only."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import runtime_storage as storage
from glossary_manager import GlossaryManager
from test_glossary_manager import fixture_pack, reference


class RuntimeStorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="storage 中文 ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        env = patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        home = patch.object(Path, "home", return_value=self.root / "home")
        home.start()
        self.addCleanup(home.stop)

    def module(self, relative):
        path = self.root / relative / "server.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return path

    def brew(self, version):
        return self.module(f"brew/Cellar/{storage.PRODUCT}/{version}/libexec/venv/lib/python3.13/site-packages")

    def seed(self, module):
        root = module.parent / "translates"
        metadata, content = fixture_pack()
        metadata["installedAt"] = "2026-10-24T00:00:00+00:00"
        folder = root / "glossaries" / metadata["id"] / metadata["version"]
        folder.mkdir(parents=True)
        (folder / f"{metadata['sha256']}.json").write_bytes(content)
        (folder / f"{metadata['sha256']}.meta.json").write_text(json.dumps(metadata))
        (root / "glossaries/catalog.json").write_text(json.dumps({"schemaVersion": 1, "packs": [metadata]}))
        work = root / "existing-task"
        (work / "output").mkdir(parents=True)
        (work / "input.pdf").write_bytes(b"source pdf")
        (work / "output/result.pdf").write_bytes(b"translated pdf")
        task = {"task_id": "existing-task", "file_name": "paper.pdf", "service": "openai",
                "status": "failed", "workspace_dir": str(work), "request_payload": {
                    "input_path": str(work / "input.pdf"), "output_dir": str(work / "output"),
                    "translation_memory_path": str(root / "translation-memory.sqlite3"),
                    "prompt": "Leave this user setting unchanged"},
                "result_files": {"dual": {"output_path": str(work / "output/result.pdf"), "filename": "result.pdf"}}}
        (root / "tasks.json").write_text(json.dumps({"tasks": [task]}))
        shutil.copy2(root / "tasks.json", root / "tasks.json.bak")
        (root / "chatgpt-auth").mkdir(mode=0o700)
        (root / "chatgpt-auth/test-settings.json").write_text('{"synthetic":true}')
        os.chmod(root / "chatgpt-auth/test-settings.json", 0o600)
        return root, metadata

    def assert_pack(self, root, metadata):
        manager = GlossaryManager(root / "glossaries", catalog={"schemaVersion": 1, "packs": [metadata]})
        try:
            with patch("glossary_manager.requests.get", side_effect=AssertionError("must work offline")):
                self.assertEqual(manager.list_packs()[0]["status"], "installed")
                terms, _ = manager.task_snapshot([reference(metadata)], [], "en", "zh-CN")
                self.assertEqual(terms[0]["target"], "神经网络")
        finally:
            manager.close()

    def test_homebrew_upgrade_and_cleanup_preserve_glossary_tasks_and_settings(self):
        old = self.brew("1.9.3")
        legacy, metadata = self.seed(old)
        original = storage._manifest(legacy)
        new = self.brew("1.9.4")
        (new.parent / "translates/glossaries").mkdir(parents=True)
        target = storage.prepare_data_directory(new)
        self.assertEqual(target, self.root / f"brew/var/{storage.PRODUCT}")
        self.assertEqual(storage._manifest(legacy), original)
        self.assert_pack(target, metadata)
        self.assertEqual((target / "migration-backup/tasks.json").read_bytes(), (legacy / "tasks.json").read_bytes())
        for name in ("tasks.json", "tasks.json.bak"):
            task = json.loads((target / name).read_text())["tasks"][0]
            self.assertEqual(task["workspace_dir"], str(target / "existing-task"))
            self.assertEqual(Path(task["request_payload"]["input_path"]).read_bytes(), b"source pdf")
            self.assertEqual(task["request_payload"]["translation_memory_path"], str(target / "translation-memory.sqlite3"))
            self.assertEqual(Path(task["result_files"]["dual"]["output_path"]).read_bytes(), b"translated pdf")
            self.assertEqual(task["request_payload"]["prompt"], "Leave this user setting unchanged")
        self.assertEqual((target / "chatgpt-auth/test-settings.json").read_text(), '{"synthetic":true}')
        if os.name != "nt":
            self.assertEqual((target / "chatgpt-auth/test-settings.json").stat().st_mode & 0o777, 0o600)
        shutil.rmtree(self.root / f"brew/Cellar/{storage.PRODUCT}")
        next_version = self.brew("1.9.5")
        self.assertEqual(storage.prepare_data_directory(next_version), target)
        self.assert_pack(target, metadata)

    def test_packaged_python_defaults_survive_environment_replacement_on_all_platforms(self):
        for platform, expected in (
            ("win32", self.root / f"local/{storage.PRODUCT}/data"),
            ("darwin", self.root / f"home/Library/Application Support/{storage.PRODUCT}"),
            ("linux", self.root / f"xdg/{storage.PRODUCT}"),
        ):
            with self.subTest(platform=platform), patch.object(sys, "platform", platform), \
                    patch.dict(os.environ, LOCALAPPDATA=str(self.root / "local"), XDG_DATA_HOME=str(self.root / "xdg")):
                old = self.module(f"{platform}/old/Lib/site-packages")
                legacy, metadata = self.seed(old)
                target = storage.prepare_data_directory(old)
                self.assertEqual(target, expected)
                shutil.rmtree(old.parent)
                new = self.module(f"{platform}/new/Lib/site-packages")
                self.assertEqual(storage.prepare_data_directory(new), target)
                self.assert_pack(target, metadata)
                self.assertFalse((new.parent / "translates").exists())

    def test_explicit_cli_and_environment_paths_skip_all_automatic_migration(self):
        module = self.brew("1.9.3")
        legacy, _ = self.seed(module)
        original = storage._manifest(legacy)
        with patch.dict(os.environ, PDF2ZH_DATA_DIR=str(self.root / "env")):
            self.assertEqual(storage.prepare_data_directory(module), self.root / "env")
            self.assertEqual(storage.prepare_data_directory(module, self.root / "cli"), self.root / "cli")
            with patch.object(sys, "argv", ["server", "--data-dir", str(self.root / "cli")]):
                storage.configure_startup_storage(module)
                self.assertEqual(os.environ["PDF2ZH_DATA_DIR"], str(self.root / "cli"))
        self.assertEqual(storage._manifest(legacy), original)
        self.assertFalse(storage.default_data_directory(module).exists())

    def test_windows_installer_data_directory_is_preserved_across_runtime_versions(self):
        data = self.root / "Windows安装/data"
        old = self.module("Windows安装/runtime/versions/old/Lib/site-packages")
        legacy, metadata = self.seed(old)
        shutil.copytree(legacy, data)
        before = storage._manifest(data)
        for version in ("old", "new"):
            module = self.module(f"Windows安装/runtime/versions/{version}/Lib/site-packages")
            self.assertEqual(storage.prepare_data_directory(module, data), data)
            self.assert_pack(data, metadata)
        self.assertEqual(storage._manifest(data), before)

    def test_source_and_docker_layout_are_unchanged(self):
        for relative in ("checkout/server", "app/server"):
            module = self.module(relative)
            self.assertEqual(storage.prepare_data_directory(module), module.parent / "translates")
            self.assertFalse((module.parent / "translates").exists())

    def test_existing_data_is_not_overwritten_or_deleted_glossary_resurrected(self):
        module = self.brew("1.9.3")
        legacy, metadata = self.seed(module)
        target = storage.prepare_data_directory(module)
        shutil.rmtree(target / "glossaries")
        (target / "custom-setting").write_text("keep")
        self.assertEqual(storage.prepare_data_directory(module), target)
        self.assertFalse((target / "glossaries").exists())
        self.assertTrue(legacy.exists())

    def test_failed_or_interrupted_migration_never_exposes_partial_data_and_can_retry(self):
        module = self.brew("1.9.3")
        legacy, metadata = self.seed(module)
        before = storage._manifest(legacy)
        target = storage.default_data_directory(module)
        with patch.object(storage.shutil, "copytree", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(RuntimeError, "original data is preserved"):
                storage.prepare_data_directory(module)
        self.assertFalse(target.exists())
        self.assertEqual(storage._manifest(legacy), before)
        self.assertEqual(list(target.parent.glob("*.migration-*")), [])
        self.assert_pack(storage.prepare_data_directory(module), metadata)

    def test_malformed_tasks_are_preserved_and_reported(self):
        module = self.brew("1.9.3")
        legacy, _ = self.seed(module)
        (legacy / "tasks.json").write_text("broken json")
        with self.assertRaisesRegex(RuntimeError, "original data is preserved"):
            storage.prepare_data_directory(module)
        self.assertEqual((legacy / "tasks.json").read_text(), "broken json")
        self.assertFalse(storage.default_data_directory(module).exists())

    def test_changing_source_during_copy_does_not_publish_an_inconsistent_snapshot(self):
        module = self.brew("1.9.3")
        legacy, _ = self.seed(module)
        copytree = shutil.copytree
        def changed(source, target, *args, **kwargs):
            result = copytree(source, target, *args, **kwargs)
            if Path(source) == legacy:
                (legacy / "concurrent-write").write_text("new data")
            return result
        with patch.object(storage.shutil, "copytree", side_effect=changed):
            with self.assertRaisesRegex(RuntimeError, "original data is preserved"):
                storage.prepare_data_directory(module)
        self.assertFalse(storage.default_data_directory(module).exists())
        self.assertEqual((legacy / "concurrent-write").read_text(), "new data")

    @unittest.skipIf(os.name == "nt", "Symlink creation requires Windows privileges")
    def test_linked_data_is_not_followed_or_silently_lost(self):
        module = self.brew("1.9.3")
        legacy, _ = self.seed(module)
        external = self.root / "external.json"
        external.write_text("preserve")
        (legacy / "linked.json").symlink_to(external)
        with self.assertRaisesRegex(RuntimeError, "original data is preserved"):
            storage.prepare_data_directory(module)
        self.assertEqual(external.read_text(), "preserve")
        self.assertFalse(storage.default_data_directory(module).exists())

    def test_ambiguous_legacy_versions_do_not_silently_drop_either_dataset(self):
        self.seed(self.brew("1.9.2"))
        self.seed(self.brew("1.9.3"))
        new = self.brew("1.9.4")
        with self.assertRaisesRegex(RuntimeError, "Multiple legacy"):
            storage.prepare_data_directory(new)
        self.assertFalse(storage.default_data_directory(new).exists())

    def test_help_does_not_migrate_or_create_data(self):
        module = self.brew("1.9.3")
        self.seed(module)
        with patch.object(sys, "argv", ["server", "--help"]):
            storage.configure_startup_storage(module)
        self.assertFalse(storage.default_data_directory(module).exists())

    def test_unreadable_legacy_directory_is_not_treated_as_empty(self):
        module = self.brew("1.9.3")
        self.seed(module)
        with patch.object(storage.os, "scandir", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                storage.prepare_data_directory(module)
        self.assertFalse(storage.default_data_directory(module).exists())


if __name__ == "__main__":
    unittest.main()
