import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import httpx

from pdf2zh_pro.translator.placeholder_repair import PlaceholderRepair
from pdf2zh_pro.translator.validation import PLACEHOLDER_PATTERN, InvalidTranslation, validate_text, canonicalize_placeholders
from pdf2zh_pro.translator.request_budget import ParagraphRequestBudget
from babeldoc.format.pdf.document_il import PdfCharacter, PdfFormula, PdfSameStyleCharacters, PdfStyle
from babeldoc.format.pdf.document_il.midend.il_translator import (
    ILTranslator, ParagraphTranslateTracker, FormulaPlaceholder, RichTextPlaceholder,
)
from diagnostic_evidence import EvidenceLedger, read_evidence
from observability import TaskMetricsCollector
import test_openai_protocol as protocol_fixtures
import test_translation_recovery as recovery_fixtures
from test_partial_batch import FixtureTranslator


SOURCE = "<style id='1'>The agreement score is measured by </style><style id='2'>F</style>{v3} score{v4}."
TARGETS = ["一致性衡量指标为", "F", "分数"]


def response(values=TARGETS):
    return json.dumps({"segments": [{"id": i, "output": text} for i, text in enumerate(values)]})


class PlaceholderRepairTests(unittest.TestCase):
    def plan(self):
        return PlaceholderRepair(SOURCE, "Original translation prompt with glossary", "zh-CN")

    def test_reconstructs_exact_tokens_order_and_whitespace(self):
        plan = self.plan()
        data = json.loads(response())
        data["segments"].reverse()  # JSON row order is not document order.
        result = plan.restore(json.dumps(data))
        self.assertEqual(result, "<style id='1'>一致性衡量指标为 </style><style id='2'>F</style>{v3} 分数{v4}.")
        self.assertEqual(PLACEHOLDER_PATTERN.findall(result), PLACEHOLDER_PATTERN.findall(SOURCE))

    def test_rejects_missing_duplicate_unknown_and_boolean_ids(self):
        rows = json.loads(response())["segments"]
        for invalid in [rows[:-1], rows + rows[:1], rows + [{"id": 88, "output": "额外"}],
                        [{"id": True, "output": "额外"}] + rows[1:]]:
            with self.subTest(rows=invalid), self.assertRaises(InvalidTranslation):
                self.plan().restore(json.dumps({"segments": invalid}))

    def test_rejects_empty_untranslated_or_invented_structure(self):
        for value in ["", None, "The agreement score is measured by", "中文{v9}", "<style id='2'>中文</style>"]:
            with self.subTest(value=value), self.assertRaises(InvalidTranslation):
                self.plan().restore(response([value, "F", "分数"]))
        for output in ["invalid json", "[]", '{"segments": {}}']:
            with self.subTest(output=output), self.assertRaises(InvalidTranslation):
                self.plan().restore(output)

    def test_numbers_punctuation_and_repeated_formulas_are_retained(self):
        source = "{v1} 2025; {v1}<style id='4'>Results</style>\n{v2}"
        plan = PlaceholderRepair(source, source, "zh-CN")
        self.assertEqual(plan.restore(response(["结果"])), "{v1} 2025; {v1}<style id='4'>结果</style>\n{v2}")

    def test_matching_counts_cannot_hide_a_formula_inside_a_style_or_unbalance_tags(self):
        source = "<style id='1'>Results</style>{v2}"
        for output in ["<style id='1'>结果{v2}</style>", "</style>结果<style id='1'>{v2}"]:
            with self.subTest(output=output), self.assertRaises(InvalidTranslation):
                validate_text(source, output, "zh-CN")

    def test_multiline_styles_and_accepted_quote_variants_retain_real_layout_objects(self):
        source = "<style id='1'>Results</style>{v2}"
        output = '<style id="1">第一行\n第二行</style>{ v 2 }'
        validate_text(source, output, "zh-CN")
        style = PdfStyle(font_id="italic", font_size=10)
        formula = PdfFormula(pdf_character=[PdfCharacter(char_unicode="x²")])
        rich = RichTextPlaceholder(1, PdfSameStyleCharacters(pdf_style=style),
                                  "<style id='1'>", "</style>", re.escape("<style id='1'>"), re.escape("</style>"))
        token = FormulaPlaceholder(2, formula, "{v2}", re.escape("{v2}"))
        translator = ILTranslator.__new__(ILTranslator)
        translator._formula_placeholder_pattern = re.compile(r"\{v\d+\}")
        translator._style_left_placeholder_pattern = re.compile(r"<style[^>]*>")
        translator._style_right_placeholder_pattern = re.compile(r"</style>")
        ti = ILTranslator.TranslateInput(source, [rich, token], PdfStyle())
        compositions = translator.parse_translate_output(ti, canonicalize_placeholders(source, output))
        self.assertEqual(len(compositions), 2)
        self.assertIs(compositions[0].pdf_same_style_unicode_characters.pdf_style, style)
        self.assertEqual(compositions[0].pdf_same_style_unicode_characters.unicode, "第一行\n第二行")
        self.assertIs(compositions[1].pdf_formula, formula)

    def test_repaired_output_uses_original_cache_key_and_does_not_call_again(self):
        engine = FixtureTranslator(lambda text: "遗漏标记" if text == SOURCE else response())
        params = {"validate_output": lambda value: validate_text(SOURCE, value, "zh-CN"),
                  "prepare_validation_retry": lambda error: (self.plan().prompt, self.plan().restore)}
        result = engine.llm_translate(SOURCE, rate_limit_params=params)
        self.assertEqual(len(engine.requests), 2)
        self.assertNotEqual(engine.requests[0], engine.requests[1])
        self.assertEqual(engine.cache.values, {SOURCE: result})
        self.assertEqual(engine.llm_translate(SOURCE, rate_limit_params=params), result)
        self.assertEqual(len(engine.requests), 2)

    def test_bad_repair_stops_at_existing_limit_without_caching(self):
        engine = FixtureTranslator(lambda text: "遗漏标记" if text == SOURCE else response(["部分译文"]))
        with self.assertRaises(InvalidTranslation):
            engine.llm_translate(SOURCE, rate_limit_params={
                "validate_output": lambda value: validate_text(SOURCE, value, "zh-CN"),
                "prepare_validation_retry": lambda error: (self.plan().prompt, self.plan().restore),
            })
        self.assertEqual(len(engine.requests), 2)
        self.assertEqual(engine.cache.values, {})

    def test_single_paragraph_wires_repair_and_checkpoint_skips_next_run(self):
        helper = recovery_fixtures.RecoveryTests()
        with helper.recovery_directory() as directory:
            paragraph, docs, _, recovery, single = helper.fixtures(directory)
            paragraph.unicode += " {v1}"
            recovery.register(docs, single.translation_config)
            engine = FixtureTranslator(lambda text: "遗漏标记" if not text.startswith("Translate the text segments")
                                      else response(["这是关于实验的完整中文段落。 "]))
            single.translate_engine = engine
            single.translate_paragraph(paragraph, docs.page[0], Mock(), ParagraphTranslateTracker(), {}, {})
            self.assertEqual(recovery.snapshot()[0]["succeeded"], 1)
            self.assertEqual(len(engine.requests), 2)
            source = "This is a complete English paragraph about the experiment. {v1}"
            self.assertIsNotNone(recovery.restored(paragraph, source))
            paragraph.unicode = source
            single.translate_paragraph(paragraph, docs.page[0], Mock(), ParagraphTranslateTracker(), {}, {})
            self.assertEqual(len(engine.requests), 2)

    def test_real_adapter_preserves_budget_and_diagnostic_retry_links(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = EvidenceLedger(Path(directory), "repair", 1)
            self.addCleanup(ledger.close, "completed")
            calls = []
            def respond(request):
                calls.append(json.loads(request.content))
                return httpx.Response(200, json=protocol_fixtures.chat("遗漏标记" if len(calls) == 1 else response()))
            translator = protocol_fixtures.ProtocolTests.translator(self, respond, protocol="chat_completions")
            translator.cache = Mock(); translator.cache.get.return_value = None
            translator.set_metrics_collector(TaskMetricsCollector(task_id="repair", provider="openai", model="m", evidence=ledger))
            budget = ParagraphRequestBudget().request([object()])
            translator.llm_translate(SOURCE, rate_limit_params={
                "request_budget": budget, "diagnostic_paragraph_refs": ["p-" + "1" * 32],
                "validate_output": lambda value: validate_text(SOURCE, value, "zh-CN"),
                "prepare_validation_retry": lambda error: (self.plan().prompt, self.plan().restore),
            })
            self.assertEqual(budget.attempts, 2)
            self.assertNotEqual(calls[0]["messages"], calls[1]["messages"])
            ledger.close("completed")
            rows = read_evidence(directory, "repair", 1, "completed")["requests"]
            rejected = next(row for row in rows if row["validationState"] == "rejected")
            accepted = next(row for row in rows if row["validationState"] == "accepted")
            self.assertEqual(accepted["parentCallId"], rejected["callId"])
            self.assertEqual(accepted["retryCause"], "output_validation")


if __name__ == "__main__":
    unittest.main()
