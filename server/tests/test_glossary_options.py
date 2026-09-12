import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from babeldoc.glossary_options import normalize_glossary_entries, glossary_entries_for_language
from pdf2zh_next.high_level import _get_glossaries
from test_server import build_pdf_payload
import server


class GlossaryOptionsTests(unittest.TestCase):
    def test_old_requests_keep_review_disabled_and_glossary_empty(self):
        self.assertEqual(server.quality_request_options({}), {"semantic_review": False, "glossary_entries": []})

    def test_invalid_rows_and_conflicting_targets_are_rejected(self):
        for value in ({}, [None], [{"source": "", "target": "目标"}],
                      [{"source": "term", "target": "目标", "tgt_lng": None}]):
            with self.subTest(value=value), self.assertRaises(server.RequestValidationError):
                server.quality_request_options({"glossaryEntries": value})
        with self.assertRaisesRegex(ValueError, "rows 1 and 2"):
            normalize_glossary_entries([
                {"source": "World  Model", "target": "世界模型", "tgt_lng": "zh_CN"},
                {"source": "world model", "target": "其他译名", "tgt_lng": "zh-cn"},
            ])

    def test_language_scoping_deduplication_and_long_phrase_priority(self):
        entries = [
            {"source": "model", "target": "模型"},
            {"source": "world model", "target": "世界模型", "tgt_lng": "zh_CN"},
            {"source": "world model", "target": "通用译名"},
            {"source": "MODEL", "target": "模型", "tgt_lng": ""},
            {"source": "model", "target": "modèle", "tgt_lng": "fr"},
        ]
        selected = glossary_entries_for_language(entries, "zh-CN")
        self.assertEqual([row["target"] for row in selected], ["世界模型", "模型"])
        self.assertEqual(entries[1]["tgt_lng"], "zh_CN")

    def test_task_owns_snapshot_not_callers_mutable_entries(self):
        entries = [{"source": "world model", "target": "世界模型"}]
        with tempfile.TemporaryDirectory() as temporary:
            prepared = server.prepare_translation_request({
                "fileContent": build_pdf_payload(), "fileName": "paper.pdf", "service": "openai",
                "semanticReview": True, "glossaryEntries": entries,
            }, Path(temporary))
        entries[0]["target"] = "later change"
        self.assertEqual(prepared.request_payload["glossary_entries"][0]["target"], "世界模型")
        self.assertTrue(prepared.request_payload["semantic_review"])

    def test_imported_entries_reach_existing_glossary_matcher(self):
        settings = SimpleNamespace(translation=SimpleNamespace(glossaries=None, lang_out="zh-CN"))
        glossaries = _get_glossaries(settings, [
            {"source": "egocentric perspective", "target": "第一人称视角", "tgt_lng": "zh-CN"},
            {"source": "world model", "target": "世界模型", "tgt_lng": "zh-CN"},
            {"source": "world model", "target": "modèle du monde", "tgt_lng": "fr"},
        ])
        self.assertEqual(glossaries[0].get_active_entries_for_text("An egocentric perspective."),
                         [("egocentric perspective", "第一人称视角")])

    def test_invalid_glossary_is_http_validation_error(self):
        response = server.create_app().test_client().post("/validate-config", json={
            "service": "openai", "glossaryEntries": [{"source": "", "target": "x"}],
        })
        self.assertEqual(response.status_code, 400)

    def test_styles_do_not_hide_terms_and_long_phrases_come_first(self):
        settings = SimpleNamespace(translation=SimpleNamespace(glossaries=None, lang_out="zh-CN"))
        glossary = _get_glossaries(settings, [
            {"source": "model", "target": "模型"},
            {"source": "world model", "target": "世界模型"},
        ])[0]
        self.assertEqual(glossary.get_active_entries_for_text("world <style id='1'>model</style>"),
                         [("world model", "世界模型"), ("model", "模型")])

    def test_csv_quoted_whitespace_has_the_same_matching_normalization(self):
        settings = SimpleNamespace(translation=SimpleNamespace(glossaries=None, lang_out="zh-CN"))
        for source in ("World  Model", "world\nmodel"):
            with self.subTest(source=source):
                glossary = _get_glossaries(settings, [{"source": source, "target": "世界模型"}])[0]
                self.assertEqual(glossary.get_active_entries_for_text("The world model works."),
                                 [(source, "世界模型")])

    def test_unicode_dedup_matches_plugin_lowercase_and_engine_normalization(self):
        entries = normalize_glossary_entries([
            {"source": "Straße", "target": "街道"}, {"source": "STRASSE", "target": "另一条目"},
        ])
        self.assertEqual(len(entries), 2)


if __name__ == "__main__":
    unittest.main()
