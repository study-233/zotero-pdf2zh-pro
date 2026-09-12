from __future__ import annotations

import asyncio
import json
import re
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from babeldoc.format.pdf.document_il.il_version_1 import (
    Document, Page, PdfCharacter, PdfParagraph, PdfParagraphComposition, PdfStyle,
)
from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator, PageTranslateTracker
from babeldoc.format.pdf.document_il.midend.il_translator_llm_only import ILTranslatorLLMOnly, BatchParagraph
from babeldoc.format.pdf.translation_recovery import TranslationRecovery
from babeldoc.translator.validation import InvalidTranslation, inspect_batch, validate_batch
from pdf2zh_next.translator.base_translator import BaseTranslator


def translated(source):
    return "这是实验段落的完整译文。" + "".join(re.findall(r"\{v\d+\}", source))


class MemoryCache:
    def __init__(self):
        self.values = {}
        self.deleted = []

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value):
        self.values[key] = value

    def delete(self, key):
        self.deleted.append(key)
        self.values.pop(key, None)


class FixtureTranslator(BaseTranslator):
    def __init__(self, respond):
        self.ignore_cache = False
        self.lang_out = "zh-CN"
        self.cache = MemoryCache()
        self.rate_limiter = Mock()
        self.metrics_collector = None
        self.translate_call_count = self.translate_cache_call_count = 0
        self.requests = []
        self.respond = respond

    def do_translate(self, text, rate_limit_params=None):
        self.requests.append(text)
        return self.respond(text)

    do_llm_translate = do_translate


class PartialBatchTests(unittest.TestCase):
    def fixture(self, stack, directory, respond):
        paragraphs = [PdfParagraph(
            debug_id=f"p-{i}", unicode=f"This is an English experiment paragraph number {i}. {{v{i}}}",
            pdf_style=PdfStyle(), layout_label="text",
            pdf_paragraph_composition=[PdfParagraphComposition(pdf_character=PdfCharacter(char_unicode="T"))],
        ) for i in range(6)]
        docs = Document(page=[Page(page_number=0, pdf_paragraph=paragraphs)])
        recovery = TranslationRecovery(Path(directory) / "paragraph-recovery.sqlite3", "test-fingerprint")
        stack.callback(recovery.close)
        config = SimpleNamespace(
            recovery=recovery, skip_references=False, min_text_length=3,
            disable_same_text_fallback=False, add_formula_placehold_hint=False,
            raise_if_cancelled=lambda: None,
        )
        recovery.register(docs, config)
        engine = FixtureTranslator(respond)
        single = ILTranslator.__new__(ILTranslator)
        single.translation_config = config
        single.translate_engine = engine
        single.use_as_fallback = False
        single.support_llm_translate = True
        single._prepare_paragraph = lambda paragraph, *args: (
            paragraph.unicode, ILTranslator.TranslateInput(paragraph.unicode, [], paragraph.pdf_style),
        )
        single.generate_prompt_for_llm = lambda text, *args: text
        batch = ILTranslatorLLMOnly.__new__(ILTranslatorLLMOnly)
        batch.translation_config = config
        batch.translate_engine = engine
        batch.il_translator = single
        batch._build_llm_prompt = lambda **kwargs: kwargs["json_input_str"]
        batch.calc_token_count = len
        batch.total_count = batch.fallback_count = batch.ok_count = 0

        def submit(fn, *args, **kwargs):
            kwargs.pop("priority", None)
            return fn(*args, **kwargs)

        def run():
            batch.translate_paragraph(
                BatchParagraph(paragraphs, [docs.page[0]] * len(paragraphs), PageTranslateTracker()),
                Mock(), {}, {}, executor=SimpleNamespace(submit=submit),
            )
            single.finish_translation_completion()
            recovery.finish()

        return paragraphs, recovery, engine, batch, run

    @staticmethod
    def partial_response(text):
        if not text.startswith("["):
            return translated(text)
        return json.dumps([
            {"id": row["id"], "output": "缺少公式的译文" if row["id"] == 2 else translated(row["input"])}
            for row in json.loads(text)
        ])

    def test_one_bad_paragraph_only_adds_one_request_and_successes_restore(self):
        with ExitStack() as stack:
            directory = stack.enter_context(tempfile.TemporaryDirectory())
            paragraphs, recovery, engine, batch, run = self.fixture(stack, directory, self.partial_response)
            sources = [p.unicode for p in paragraphs]
            run()
            self.assertEqual(len(engine.requests), 2)
            self.assertEqual(engine.requests[1], sources[2])
            self.assertEqual(batch.fallback_count, 1)
            self.assertEqual(batch.ok_count, 5)
            self.assertEqual(recovery.snapshot()[0]["succeeded"], 6)
            self.assertIn(engine.requests[0], engine.cache.values)
            self.assertFalse(inspect_batch(engine.cache.values[engine.requests[0]], sources, "zh-CN").invalid_outputs)
            self.assertEqual([p.unicode for p in paragraphs], [translated(s) for s in sources])
            recovery.close()
            _, restored, second_engine, _, rerun = self.fixture(stack, directory, self.partial_response)
            rerun()
            self.assertEqual(second_engine.requests, [])
            self.assertEqual(restored.snapshot()[0]["succeeded"], 6)

    def test_structure_errors_retry_the_batch_before_falling_back(self):
        for malformed in ["not json", '[{"id":0,"output":"译文"}]',
                          json.dumps([{"id": 0, "output": "译文"}] * 6)]:
            with self.subTest(malformed=malformed), ExitStack() as stack:
                directory = stack.enter_context(tempfile.TemporaryDirectory())
                _, recovery, engine, batch, run = self.fixture(
                    stack, directory, lambda text: malformed if text.startswith("[") else translated(text),
                )
                run()
                self.assertEqual(len(engine.requests), 8)
                self.assertEqual(batch.fallback_count, 6)
                self.assertEqual(recovery.snapshot()[0]["succeeded"], 6)
                self.assertIn(engine.requests[0], engine.cache.values)

    def test_good_rows_are_committed_before_a_cancelled_single_repair(self):
        with ExitStack() as stack:
            directory = stack.enter_context(tempfile.TemporaryDirectory())

            def respond(text):
                if text.startswith("["):
                    return self.partial_response(text)
                self.assertEqual(recovery.snapshot()[0]["succeeded"], 5)
                raise asyncio.CancelledError

            paragraphs, recovery, _, _, run = self.fixture(stack, directory, respond)
            sources = [p.unicode for p in paragraphs]
            with self.assertRaises(asyncio.CancelledError):
                run()
            self.assertEqual(recovery.snapshot()[0]["pending"], 1)
            recovery.close()
            resumed, restored, _, _, _ = self.fixture(stack, directory, self.partial_response)
            for index, paragraph in enumerate(resumed):
                self.assertEqual(restored.restored(paragraph, sources[index]),
                                 None if index == 2 else translated(sources[index]))

    def test_partial_cached_response_is_evicted_but_valid_rows_remain_usable(self):
        source = "This is an English experiment paragraph. {v1}"
        sources = [source, source + " Another sentence."]
        partial = json.dumps([{"id": 0, "output": translated(source)}, {"id": 1, "output": "缺少公式"}])
        engine = FixtureTranslator(lambda text: self.fail("must reuse the valid cached rows"))
        engine.cache.set("batch", partial)
        inspect = lambda output: inspect_batch(output, sources, "zh-CN")
        result = engine.llm_translate("batch", rate_limit_params={
            "validate_output": inspect,
            "cache_output_if": lambda output: not inspect(output).invalid_outputs,
        })
        self.assertEqual(inspect(result).valid_outputs, {0: translated(source)})
        self.assertEqual(inspect(result).invalid_outputs, {1: "placeholder_mismatch"})
        self.assertEqual(engine.cache.deleted, ["batch"])
        self.assertNotIn("batch", engine.cache.values)
        self.assertEqual(engine.requests, [])

    def test_complete_batches_still_cache_and_strict_validator_stays_strict(self):
        sources = ["This is a complete English experiment paragraph."]
        response = json.dumps([{"id": 0, "output": translated(sources[0])}])
        engine = FixtureTranslator(lambda text: response)
        params = {
            "validate_output": lambda output: inspect_batch(output, sources, "zh-CN"),
            "cache_output_if": lambda output: not inspect_batch(output, sources, "zh-CN").invalid_outputs,
        }
        engine.llm_translate("batch", rate_limit_params=params)
        engine.llm_translate("batch", rate_limit_params=params)
        self.assertEqual(engine.requests, ["batch"])
        with self.assertRaises(InvalidTranslation):
            validate_batch(json.dumps([{"id": 0, "output": sources[0]}]), sources, "zh-CN")

    def test_duplicate_ids_are_structural_even_if_the_first_row_is_invalid(self):
        with self.assertRaisesRegex(InvalidTranslation, "invalid_or_duplicate_paragraph_id"):
            inspect_batch('[{"id":0,"output":""},{"id":0,"output":"译文"}]', ["first", "second"])
