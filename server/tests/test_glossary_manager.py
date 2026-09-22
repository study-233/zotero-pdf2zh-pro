from __future__ import annotations

import copy
import hashlib
import itertools
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from glossary_manager import GlossaryError, GlossaryManager


def fixture_pack(pack_id="computing", version="1", rows=None):
    entries = rows or [{"source": "neural network", "target": "神经网络", "tgt_lng": "zh-CN"}]
    sources = [{"name": "Test source", "url": "https://example.org/terms",
                "license": "CC0-1.0", "licenseUrl": "https://creativecommons.org/publicdomain/zero/1.0/"}]
    body = {"schemaVersion": 1, "id": pack_id, "version": version, "sourceLang": "en",
            "targetLang": "zh-CN", "sources": sources, "entries": entries}
    content = json.dumps(body, ensure_ascii=False).encode("utf-8")
    metadata = {key: body[key] for key in ("id", "version", "sourceLang", "targetLang", "sources")}
    metadata.update(name={"zhCN": "测试", "en": "Test"}, sha256=hashlib.sha256(content).hexdigest(),
                    sizeBytes=len(content), entryCount=len(entries),
                    url=f"https://raw.githubusercontent.com/study-233/zotero-pdf2zh-pro/{'a' * 40}/{pack_id}.json")
    return metadata, content


class Response:
    def __init__(self, content):
        self.content = content

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def raise_for_status(self):
        return None

    def iter_content(self, size):
        for offset in range(0, len(self.content), size):
            yield self.content[offset:offset + size]


def reference(metadata):
    return {key: metadata[key] for key in ("id", "version", "sha256")}


class GlossaryManagerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "glossaries"

    def manager(self, *metadata):
        manager = GlossaryManager(self.root, catalog={"schemaVersion": 1, "packs": list(metadata)},
                                  catalog_url="https://raw.githubusercontent.com/study-233/zotero-pdf2zh-pro/glossary-data/catalog.json")
        self.addCleanup(manager.close)
        return manager

    def install(self, manager, metadata, content):
        with patch("glossary_manager.requests.get", return_value=Response(content)):
            manager.download(metadata["id"])
            manager._downloads[metadata["id"]]["thread"].join(timeout=2)
        self.assertFalse(manager._downloads[metadata["id"]]["thread"].is_alive())
        return next(pack for pack in manager.list_packs() if pack["id"] == metadata["id"])

    def test_fresh_manager_has_only_catalog_metadata_and_no_terms(self):
        metadata, _ = fixture_pack()
        manager = self.manager(metadata)
        self.assertFalse(self.root.exists())
        packs = manager.list_packs()
        self.assertEqual(packs[0]["status"], "not_downloaded")
        self.assertEqual(packs[0]["installedVersions"], [])
        self.assertEqual(list(self.root.rglob("*.json")), [])
        self.assertNotIn("entries", packs[0])

    def test_download_is_verified_atomic_and_restores_offline(self):
        metadata, content = fixture_pack()
        manager = self.manager(metadata)
        pack = self.install(manager, metadata, content)
        self.assertEqual(pack["status"], "installed")
        self.assertEqual(pack["installedVersions"][0]["sha256"], metadata["sha256"])
        self.assertEqual(pack["download"]["receivedBytes"], len(content))
        self.assertEqual(list(self.root.rglob("*.part")), [])
        restored = self.manager(metadata)
        with patch("glossary_manager.requests.get", side_effect=AssertionError("offline")):
            self.assertEqual(restored.list_packs()[0]["status"], "installed")
            rows, saved = restored.task_snapshot([reference(metadata)], [], "en", "zh-CN")
        self.assertEqual(rows[0]["target"], "神经网络")
        self.assertEqual(saved[0]["sources"], metadata["sources"])

    def test_duplicate_download_cancel_and_uninstall_do_not_expose_partial_pack(self):
        metadata, content = fixture_pack()
        manager = self.manager(metadata)
        entered, release = threading.Event(), threading.Event()

        class SlowResponse(Response):
            def iter_content(self, size):
                yield content[:10]
                entered.set()
                release.wait(2)
                yield content[10:]

        with patch("glossary_manager.requests.get", return_value=SlowResponse(content)) as fetch:
            try:
                manager.download(metadata["id"])
                self.assertTrue(entered.wait(1))
                self.assertEqual(manager.download(metadata["id"])["status"], "downloading")
                self.assertEqual(fetch.call_count, 1)
                self.assertEqual(manager.list_packs()[0]["installedVersions"], [])
                with self.assertRaisesRegex(GlossaryError, "not installed"):
                    manager.task_snapshot([reference(metadata)], [], "en", "zh-CN")
                with self.assertRaisesRegex(GlossaryError, "Cancel"):
                    manager.uninstall(metadata["id"])
                self.assertEqual(manager.cancel(metadata["id"])["download"]["state"], "cancelling")
            finally:
                release.set()
                manager._downloads[metadata["id"]]["thread"].join(2)
        self.assertEqual(manager.list_packs()[0]["status"], "not_downloaded")
        self.assertEqual(list(self.root.rglob("*.part")), [])
        self.assertEqual(manager.uninstall(metadata["id"])["installedVersions"], [])

    def test_failed_update_preserves_installed_version_and_success_preserves_old_snapshot(self):
        old, old_content = fixture_pack(version="1")
        new, new_content = fixture_pack(version="2")
        manager = self.manager(old)
        self.install(manager, old, old_content)
        manager._catalog = {new["id"]: new}
        self.assertEqual(manager.list_packs()[0]["status"], "update_available")
        damaged = new_content.replace("神经".encode(), "人工".encode())
        failed = self.install(manager, new, damaged)
        self.assertEqual(failed["status"], "failed")
        self.assertIn("checksum", failed["download"]["error"])
        self.assertEqual(failed["installedVersions"][0]["version"], "1")
        current = self.install(manager, new, new_content)
        self.assertEqual([row["version"] for row in current["installedVersions"]], ["2", "1"])
        rows, _ = manager.task_snapshot([reference(old)], [], "en", "zh-CN")
        self.assertEqual(rows[0]["target"], "神经网络")
        manager._content_path(old).write_bytes(b"corrupt old version")
        self.assertEqual(manager.list_packs()[0]["status"], "installed")
        self.assertEqual(len(manager.list_packs()[0]["installedVersions"]), 1)
        manager.uninstall(new["id"])
        self.assertEqual(rows[0]["target"], "神经网络")
        with self.assertRaisesRegex(GlossaryError, "not installed"):
            manager.task_snapshot([reference(old)], [], "en", "zh-CN")

    def test_bad_size_schema_and_language_are_not_installed(self):
        for change in ("oversize", "bad_schema", "bad_language"):
            with self.subTest(change=change):
                metadata, content = fixture_pack(pack_id=change)
                if change == "oversize":
                    content += b"x"
                else:
                    data = json.loads(content)
                    if change == "bad_schema":
                        data["schemaVersion"] = 2
                    else:
                        data["entries"][0]["tgt_lng"] = "fr"
                    content = json.dumps(data).encode()
                    metadata.update(sizeBytes=len(content), sha256=hashlib.sha256(content).hexdigest())
                manager = self.manager(metadata)
                result = self.install(manager, metadata, content)
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["installedVersions"], [])

    def test_restart_discards_partial_files_and_rejects_corrupted_cache(self):
        metadata, content = fixture_pack()
        manager = self.manager(metadata)
        self.install(manager, metadata, content)
        partial = manager._content_path(metadata).with_suffix(".json.part")
        partial.write_bytes(b"partial")
        manager._content_path(metadata).write_bytes(b"corrupt")
        restored = self.manager(metadata)
        self.assertEqual(restored.list_packs()[0]["status"], "not_downloaded")
        self.assertFalse(partial.exists())

    def test_corruption_after_install_is_visible_and_redownload_repairs_it(self):
        metadata, content = fixture_pack()
        manager = self.manager(metadata)
        self.install(manager, metadata, content)
        manager._content_path(metadata).write_bytes(b"corrupt")
        self.assertEqual(manager.list_packs()[0]["status"], "failed")
        self.assertEqual(manager.list_packs()[0]["installedVersions"], [])
        self.assertEqual(self.install(manager, metadata, content)["status"], "installed")
        manager._content_path(metadata).write_bytes(b"missing")
        # The download action also repairs corruption before another list/read call.
        self.assertEqual(self.install(manager, metadata, content)["status"], "installed")

    def test_catalog_refresh_is_explicit_verified_and_keeps_previous_on_failure(self):
        old, _ = fixture_pack(version="1")
        new, _ = fixture_pack(version="2")
        manager = self.manager(old)
        with patch("glossary_manager.requests.get", return_value=Response(b"invalid")):
            with self.assertRaisesRegex(GlossaryError, "Could not update"):
                manager.refresh_catalog()
        self.assertEqual(manager.list_packs()[0]["version"], "1")
        with patch("glossary_manager.requests.get", return_value=Response(b'{"schemaVersion":1,"packs":[]}')):
            with self.assertRaisesRegex(GlossaryError, "cannot remove"):
                manager.refresh_catalog()
        self.assertEqual(manager.list_packs()[0]["version"], "1")
        catalog = {"schemaVersion": 1, "packs": [new]}
        with patch("glossary_manager.requests.get", return_value=Response(json.dumps(catalog).encode())):
            self.assertEqual(manager.refresh_catalog()[0]["version"], "2")
        restored = self.manager(old)
        with patch("glossary_manager.requests.get", side_effect=AssertionError("offline")):
            self.assertEqual(restored.list_packs()[0]["version"], "2")

    def test_catalog_rejects_arbitrary_urls_paths_and_missing_attribution(self):
        metadata, _ = fixture_pack()
        for field, value in (("id", "../bad"), ("version", "../bad"),
                             ("url", "http://127.0.0.1/secret"), ("sources", []),
                             ("url", metadata["url"].replace("a" * 40, "main"))):
            invalid = {**metadata, field: value}
            with self.subTest(field=field), self.assertRaises(GlossaryError):
                self.manager(invalid)

    def test_merge_conflict_omission_and_custom_override_are_order_independent(self):
        fixtures = [fixture_pack(pack_id=pack_id, rows=[
            {"source": " MODEL  ", "target": target, "tgt_lng": "zh-CN"},
            {"source": "neural network", "target": "神经网络", "tgt_lng": "zh-CN"},
        ]) for pack_id, target in (("a", "甲"), ("b", "乙"), ("c", "甲"))]
        manager = self.manager(*(item[0] for item in fixtures))
        for metadata, content in fixtures:
            self.install(manager, metadata, content)
        references = [reference(item[0]) for item in fixtures]
        for order in itertools.permutations(references):
            rows, _ = manager.task_snapshot(list(order), [], "en", "zh-CN")
            self.assertEqual(rows, [{"source": "neural network", "target": "神经网络", "tgt_lng": "zh-cn"}])
            custom = [{"source": "model", "target": "自定义", "tgt_lng": ""}]
            rows, _ = manager.task_snapshot(list(order), custom, "en", "zh-CN")
            self.assertEqual(rows[0], custom[0])
            self.assertEqual(len(rows), 2)
            rows[0]["target"] = "later"
            self.assertEqual(custom[0]["target"], "自定义")

    def test_language_aliases_scope_and_custom_language_specific_precedence(self):
        from babeldoc.glossary_options import glossary_entries_for_language
        metadata, content = fixture_pack()
        manager = self.manager(metadata)
        self.install(manager, metadata, content)
        for source, target in (("en", "zh"), ("EN_us", "ZH_CN"), ("en-GB", "zh-Hans")):
            rows, _ = manager.task_snapshot([reference(metadata)], [], source, target)
            self.assertEqual(len(glossary_entries_for_language(rows, target)), 1)
        for source, target in (("fr", "zh-CN"), ("en", "zh-TW"), ("en", "ja")):
            rows, _ = manager.task_snapshot([reference(metadata)], [], source, target)
            self.assertEqual(rows, [])
        custom = [
            {"source": "neural network", "target": "默认", "tgt_lng": ""},
            {"source": "neural network", "target": "指定", "tgt_lng": "zh-cn"},
            {"source": "neural network", "target": "French", "tgt_lng": "fr"},
        ]
        rows, _ = manager.task_snapshot([reference(metadata)], custom, "en", "zh-CN")
        self.assertEqual(rows, custom)
        self.assertEqual(glossary_entries_for_language(rows, "zh-CN")[0]["target"], "指定")
        rows, _ = manager.task_snapshot([reference(metadata)], custom[2:], "en", "zh-CN")
        self.assertEqual(len(rows), 2)

    def test_invalid_missing_or_modified_references_are_rejected(self):
        metadata, content = fixture_pack()
        manager = self.manager(metadata)
        self.install(manager, metadata, content)
        for references in (None, {}, [None], [{"id": "x"}],
                           [{**reference(metadata), "sha256": "f" * 64}],
                           [{**reference(metadata), "version": "unknown"}]):
            with self.subTest(references=references), self.assertRaises(GlossaryError):
                manager.task_snapshot(references, [], "en", "zh-CN")
        manager._content_path(metadata).write_bytes(b"corrupt")
        with self.assertRaisesRegex(GlossaryError, "checksum"):
            manager.task_snapshot([reference(metadata)], [], "en", "zh-CN")


class GlossaryRouteTests(unittest.TestCase):
    def setUp(self):
        import server
        self.server = server
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.metadata, self.content = fixture_pack()
        self.manager = GlossaryManager(Path(self.temporary.name) / "glossaries",
            catalog={"schemaVersion": 1, "packs": [self.metadata]})
        self.addCleanup(self.manager.close)
        self.patch = patch.object(server, "GLOSSARY_MANAGER", self.manager)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.client = server.create_app().test_client()

    def test_catalog_download_and_mutation_routes(self):
        self.assertTrue(self.client.get("/health").json["capabilities"]["glossaryPacks"])
        self.assertEqual(self.client.get("/glossaries").json["packs"][0]["status"], "not_downloaded")
        self.assertEqual(self.client.post("/glossaries/missing/download").status_code, 404)
        self.assertEqual(self.client.post("/glossaries/computing/download", json={"version": "unknown"}).status_code, 400)
        self.assertEqual(self.client.post("/glossaries/computing/download", json=[]).status_code, 400)
        self.assertEqual(self.client.post("/glossaries/computing/download", json={"url": "http://localhost"}).status_code, 400)
        with patch("glossary_manager.requests.get", return_value=Response(self.content)):
            self.assertEqual(self.client.post("/glossaries/computing/download").status_code, 202)
            self.manager._downloads["computing"]["thread"].join(2)
        self.assertEqual(self.client.post("/glossaries/computing/cancel").status_code, 200)
        self.assertEqual(self.client.delete("/glossaries/computing").json["pack"]["status"], "not_downloaded")
        with patch.object(self.manager, "refresh_catalog", return_value=[]) as refresh:
            self.assertEqual(self.client.post("/glossaries/check-updates").status_code, 200)
            refresh.assert_called_once_with()

    def test_task_request_captures_pack_terms_and_sources_before_uninstall(self):
        from test_server import build_pdf_payload
        with patch("glossary_manager.requests.get", return_value=Response(self.content)):
            self.manager.download("computing")
            self.manager._downloads["computing"]["thread"].join(2)
        with tempfile.TemporaryDirectory() as workspace:
            request = {"fileContent": build_pdf_payload(), "fileName": "paper.pdf", "service": "openai",
                       "glossaryPacks": [reference(self.metadata)]}
            prepared = self.server.prepare_translation_request(request, Path(workspace))
        original = copy.deepcopy(prepared.request_payload)
        self.manager.uninstall("computing")
        request["glossaryPacks"][0]["version"] = "later"
        self.assertEqual(prepared.request_payload, original)
        self.assertEqual(original["glossary_entries"][0]["target"], "神经网络")
        self.assertEqual(original["glossary_packs"][0]["sources"], self.metadata["sources"])
        response = self.client.post("/validate-config", json={"service": "openai", "glossaryPacks": [reference(self.metadata)]})
        self.assertEqual(response.status_code, 400)

    def test_empty_pack_selection_does_not_touch_glossary_storage(self):
        with patch.object(self.manager, "task_snapshot", side_effect=AssertionError("not needed")):
            self.assertEqual(self.server.quality_request_options({"glossaryPacks": []}),
                             {"glossary_entries": [], "semantic_review": False})
        self.assertFalse(self.manager.root.exists())

    def test_runtime_paths_initialize_glossaries_under_effective_data_root(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            partial = root / "glossaries" / "computing" / "1" / "old.json.part"
            partial.parent.mkdir(parents=True)
            partial.write_bytes(b"incomplete")
            with patch.object(self.server, "TRANSLATES_DIR"), patch.object(self.server, "TASK_MANAGER", Mock()):
                try:
                    self.server.configure_runtime_paths(root)
                    self.assertEqual(self.server.GLOSSARY_MANAGER.root, root / "glossaries")
                    self.assertFalse(partial.exists())
                finally:
                    self.server.GLOSSARY_MANAGER.close()
                    self.server.TASK_MANAGER.close()


if __name__ == "__main__":
    unittest.main()
