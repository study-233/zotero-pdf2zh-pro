import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from babeldoc.format.pdf.document_il.midend.automatic_term_extractor import (
    AutomaticTermExtractor,
    BatchParagraph,
    PageTermExtractTracker,
)
from babeldoc.translator.validation import InvalidTranslation
from pdf2zh_next.translator.base_translator import BaseTranslator


class MemoryCache:
    def __init__(self, cached=None):
        self.values = {}
        self.initial_value = cached
        self.deleted = []
        self.writes = []

    def get(self, key):
        return self.values.get(key, self.initial_value)

    def delete(self, key):
        self.deleted.append(key)
        self.initial_value = None
        self.values.pop(key, None)

    def set(self, key, value):
        self.values[key] = value
        self.writes.append((key, value))


class TermTranslator(BaseTranslator):
    def __init__(self, outputs, cached=None):
        self.ignore_cache = False
        self.cache = MemoryCache(cached)
        self.outputs = iter(outputs)
        self.requests = 0
        self.translate_call_count = 0
        self.translate_cache_call_count = 0
        self.metrics_collector = None
        self.rate_limiter = SimpleNamespace(wait=Mock())

    def do_translate(self, text, rate_limit_params=None):
        raise AssertionError("term extraction must use llm_translate")

    def do_llm_translate(self, text, rate_limit_params=None):
        self.requests += 1
        return next(self.outputs)


class TermValidationTests(unittest.TestCase):
    def fixture(self, outputs=(), cached=None):
        translator = TermTranslator(outputs, cached)
        extractor = AutomaticTermExtractor.__new__(AutomaticTermExtractor)
        extractor.translate_engine = translator
        extractor.translation_config = SimpleNamespace(
            raise_if_cancelled=lambda: None, lang_out="zh"
        )
        pairs = []
        extractor.shared_context = SimpleNamespace(
            user_glossaries=[],
            add_raw_extracted_term_pair=lambda src, tgt: pairs.append((src, tgt)),
        )
        return extractor, translator, pairs

    def extract(self, extractor):
        paragraphs = BatchParagraph(
            [SimpleNamespace(unicode="A discussion of neural networks.")],
            PageTermExtractTracker(),
        )
        progress = SimpleNamespace(advance=Mock())
        extractor.extract_terms_from_paragraphs(paragraphs, progress, 10)
        progress.advance.assert_called_once_with(1)
        return paragraphs.tracker

    def test_invalid_cached_terms_are_evicted_before_regeneration(self):
        good = '[{"src": "neural networks", "tgt": "神经网络"}]'
        for cached in ("not JSON", '[{"src": 42, "tgt": "神经网络"}]'):
            with self.subTest(cached=cached):
                extractor, translator, pairs = self.fixture([good], cached)
                self.extract(extractor)
                self.assertEqual(translator.requests, 1)
                self.assertEqual(len(translator.cache.deleted), 1)
                self.assertEqual([value for _, value in translator.cache.writes], [good])
                self.assertEqual(pairs, [("neural networks", "神经网络")])
                self.assertEqual(translator.translate_cache_call_count, 0)

    def test_bad_json_is_retried_once_and_only_valid_output_is_cached(self):
        good = '```json\n[{"src":" neural networks ","tgt":" 神经网络 "}]\n```'
        extractor, translator, pairs = self.fixture(["not JSON", good])
        tracker = self.extract(extractor)
        self.assertEqual(translator.requests, 2)
        self.assertEqual([value for _, value in translator.cache.writes], [good])
        self.assertEqual(pairs, [("neural networks", "神经网络")])
        self.assertEqual(tracker.output, good)

    def test_persistent_invalid_json_is_not_cached_or_applied(self):
        extractor, translator, pairs = self.fixture(["bad JSON", "still bad"])
        with self.assertLogs(
            "babeldoc.format.pdf.document_il.midend.automatic_term_extractor",
            level="WARNING",
        ):
            self.extract(extractor)
        self.assertEqual(translator.requests, 2)
        self.assertEqual(translator.cache.writes, [])
        self.assertEqual(pairs, [])

    def test_empty_term_array_is_cached_and_reused(self):
        extractor, translator, pairs = self.fixture(["[]"])
        self.extract(extractor)
        self.extract(extractor)
        self.assertEqual(translator.requests, 1)
        self.assertEqual(translator.translate_cache_call_count, 1)
        self.assertEqual([value for _, value in translator.cache.writes], ["[]"])
        self.assertEqual(pairs, [])

    def test_single_term_object_and_existing_json_wrappers_remain_supported(self):
        good = '{"src":"neural networks","tgt":"神经网络"}'
        for output in (good, f"<json>{good}</json>", f"```\n{good}\n```"):
            with self.subTest(output=output):
                extractor, translator, pairs = self.fixture([output])
                self.extract(extractor)
                self.assertEqual(translator.requests, 1)
                self.assertEqual(pairs, [("neural networks", "神经网络")])
                self.assertEqual(len(translator.cache.writes), 1)

    def test_invalid_term_fields_reject_entire_response_before_caching(self):
        good = {"src": "neural networks", "tgt": "神经网络"}
        for field in ("src", "tgt"):
            for invalid in (None, 42, True, {}, [], "", " \t "):
                with self.subTest(field=field, invalid=invalid):
                    bad = {**good, field: invalid}
                    output = json.dumps([good, bad])
                    extractor, translator, pairs = self.fixture([output, output])
                    with self.assertRaisesRegex(
                        InvalidTranslation, "^invalid_term_structure$"
                    ):
                        translator.llm_translate(
                            "terms",
                            rate_limit_params={
                                "validate_output": extractor._parse_term_response
                            },
                        )
                    self.assertEqual(translator.requests, 2)
                    self.assertEqual(translator.cache.writes, [])
                    self.assertEqual(pairs, [])

    def test_invalid_top_level_or_missing_fields_use_fixed_error_labels(self):
        extractor, _, _ = self.fixture()
        for output in ("null", "7", '"terms"', "{}", "[null]", '[{"src":"term"}]'):
            with self.subTest(output=output), self.assertRaisesRegex(
                InvalidTranslation, "^invalid_term_structure$"
            ):
                extractor._parse_term_response(output)
        for output in (None, 42, "not JSON", ""):
            with self.subTest(output=output), self.assertRaisesRegex(
                InvalidTranslation, "^invalid_term_json$"
            ):
                extractor._parse_term_response(output)


if __name__ == "__main__":
    unittest.main()
