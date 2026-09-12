import copy
import json
import re
import tempfile
import unicodedata
import unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from babeldoc.format.pdf.document_il import (
    Box, Document, Page, PdfCharacter, PdfFont, PdfFormula, PdfLine, PdfParagraph,
    PdfParagraphComposition, PdfStyle,
)
from babeldoc.format.pdf.document_il.midend.body_input import BodyInputPlan
from babeldoc.format.pdf.document_il.midend.il_translator import (
    ILTranslator, PageTranslateTracker, ParagraphTranslateTracker,
)
from babeldoc.format.pdf.document_il.midend.il_translator_llm_only import (
    BatchParagraph, ILTranslatorLLMOnly,
)
from babeldoc.format.pdf.document_il.utils.layout_helper import get_char_unicode_string
from babeldoc.format.pdf.document_il.utils.formular_helper import is_formulas_start_char
from babeldoc.format.pdf.translation_recovery import TranslationRecovery
from babeldoc.translator.validation import InvalidTranslation, validate_text


def paragraph(text, index=0, label="text"):
    return PdfParagraph(
        debug_id=f"body-{index}", unicode=text, layout_label=label, pdf_style=PdfStyle(),
        box=Box(x=index * 220, x2=index * 220 + 200, y=50, y2=100 + index * 100),
        pdf_paragraph_composition=[PdfParagraphComposition(
            pdf_character=PdfCharacter(char_unicode=text),
        )],
    )


def plan_for(*texts, pages=False, evidence=""):
    paragraphs = [paragraph(text, i) for i, text in enumerate(texts)]
    if pages:
        docs = Document(page=[Page(page_number=i, pdf_paragraph=[p]) for i, p in enumerate(paragraphs)])
    else:
        docs = Document(page=[Page(page_number=0, pdf_paragraph=paragraphs[:])])
    if evidence:
        docs.page[-1].pdf_paragraph.append(paragraph(evidence, len(paragraphs)))
    prepared = {id(p): p.unicode for page in docs.page for p in page.pdf_paragraph}
    return docs, paragraphs, BodyInputPlan(docs, prepared)


class BodyInputTests(unittest.TestCase):
    def test_article_moves_only_in_working_input_and_markers_stay_owned(self):
        texts = ("<style id='1'>The experiment ends here. A</style>{v9}",
                 "<style id='3'>core idea is useful.</style>")
        # A formula after the article makes the boundary ambiguous: leave it alone.
        _, ps, plan = plan_for(*texts)
        self.assertEqual(plan.get(ps[0]).text, texts[0])
        texts = ("{v9}<style id='1'>The experiment ends here. A</style>", texts[1])
        docs, ps, plan = plan_for(*texts)
        original = copy.deepcopy(docs)
        self.assertEqual(plan.get(ps[0]).text, "{v9}<style id='1'>The experiment ends here. </style>")
        self.assertEqual(plan.get(ps[1]).text, "<style id='3'>A core idea is useful.</style>")
        for p, source in zip(ps, texts):
            self.assertEqual(Counter(re.findall(r"<[^>]+>|\{v\d+\}", source)),
                             Counter(re.findall(r"<[^>]+>|\{v\d+\}", plan.get(p).text)))
        self.assertEqual(docs, original)
        self.assertTrue(plan.get(ps[1]).continuation_risk)
        self.assertNotIn("{v9}", str(plan.get(ps[1]).context))

    def test_cross_page_word_suffix_needs_complete_document_word(self):
        texts = ("We test more complex scenar-", "ios. The results validate this strategy.")
        _, ps, plan = plan_for(*texts, pages=True)
        self.assertEqual([plan.get(p).text for p in ps], list(texts))
        _, ps, plan = plan_for(*texts, pages=True, evidence="Additional scenarios support our findings.")
        self.assertEqual(plan.get(ps[0]).text, "We test more complex scenarios.")
        self.assertEqual(plan.get(ps[1]).text, " The results validate this strategy.")

    def test_internal_wraps_require_evidence_and_never_cross_tokens(self):
        text = "Our resid-\nual model is real- world. resid- {v1}ual <code>resid-\nual</code>"
        _, ps, plan = plan_for(text, evidence="The residual model is useful.")
        self.assertEqual(plan.get(ps[0]).text,
                         "Our residual model is real- world. resid- {v1}ual <code>resid-\nual</code>")
        _, ps, plan = plan_for("Our resid- ual model.", evidence="The residual model is useful.")
        self.assertEqual(plan.get(ps[0]).text, "Our resid- ual model.")

    def test_soft_hyphens_do_not_need_document_evidence(self):
        _, ps, plan = plan_for("An inter\u00adnational experiment and an inter\u00ad\nnational study.")
        self.assertEqual(plan.get(ps[0]).text, "An international experiment and an international study.")

    def test_glossary_evidence_repairs_only_actual_line_wraps(self):
        docs, ps, plan = plan_for("A cam-\nera captures images, while cam- era stays literal.")
        self.assertEqual(plan.get(ps[0]).text, ps[0].unicode)
        plan = BodyInputPlan(docs, {id(ps[0]): ps[0].unicode}, evidence=["camera"])
        self.assertEqual(plan.get(ps[0]).text,
                         "A camera captures images, while cam- era stays literal.")
        translator = ILTranslator.__new__(ILTranslator)
        translator.support_llm_translate = True
        translator.translation_config = SimpleNamespace(
            disable_rich_text_translate=False, min_text_length=3, raise_if_cancelled=lambda: None,
        )
        translator._cached_glossaries = [SimpleNamespace(entries=[SimpleNamespace(source="camera")])]
        translator.get_translate_input = lambda p, *args: ILTranslator.TranslateInput(p.unicode, [])
        translator.prepare_body_input_plan(docs)
        self.assertEqual(translator.body_input_plan.get(ps[0]).text, plan.get(ps[0]).text)

    def test_headings_formulas_page_gaps_and_geometry_block_reassignment(self):
        for barrier in ("title", "figure_caption", "table", "reference"):
            docs, ps, _ = plan_for("An experiment finished. A", "core idea follows.")
            docs.page[0].pdf_paragraph.insert(1, paragraph("New section", 2, barrier))
            plan = BodyInputPlan(docs, {id(p): p.unicode for p in ps})
            self.assertEqual(plan.get(ps[1]).text, ps[1].unicode)
        docs, ps, _ = plan_for("An experiment finished. A", "core idea follows.", pages=True)
        docs.page[1].page_number = 3
        plan = BodyInputPlan(docs, {id(p): p.unicode for p in ps})
        self.assertFalse(plan.get(ps[0]).continuation_risk)
        docs, ps, _ = plan_for("An experiment finished. A", "core idea follows.")
        ps[1].box.x = 10
        plan = BodyInputPlan(docs, {id(p): p.unicode for p in ps})
        self.assertEqual(plan.get(ps[0]).text, ps[0].unicode)

    def test_variable_a_and_suffix_only_fragments_are_not_moved(self):
        for left, right in (("We use the matrix A", "core calculations follow."),
                            ("More complex scenar-", "ios.")):
            _, ps, plan = plan_for(left, right, evidence="These scenarios are challenging.")
            self.assertEqual([plan.get(p).text for p in ps], [left, right])

    def test_long_continuation_only_gets_read_only_context(self):
        texts = ("The proposed system can generalize to", "previously unseen environments.")
        _, ps, plan = plan_for(*texts)
        self.assertEqual([plan.get(p).text for p in ps], list(texts))
        self.assertEqual(plan.get(ps[0]).context, {"next_fragment": texts[1]})
        # Worker output changes cannot alter the precomputed plan or context.
        ps[1].unicode = "已经翻译的段落"
        self.assertEqual(plan.get(ps[0]).context, {"next_fragment": texts[1]})
        with self.assertRaises(TypeError):
            plan.entries[id(ps[0])] = None

    def test_continuation_groups_and_order_are_fixed_before_concurrent_registration(self):
        _, ps, plan = plan_for("This method generalizes to", "previously unseen but",
                               "related environments.", pages=True)
        self.assertEqual([plan.get(p).order_key for p in ps], [0, 1, 2])
        self.assertEqual([plan.get(p).continuation_group for p in ps], [0, 0, 0])

    def test_preparation_uses_each_page_fonts_and_reuses_snapshot(self):
        docs, ps, _ = plan_for("An experiment finished. A", "core idea follows.", pages=True)
        fonts = [PdfFont(font_id="F1"), PdfFont(font_id="F1")]
        for page, font in zip(docs.page, fonts):
            page.pdf_font = [font]
        translator = ILTranslator.__new__(ILTranslator)
        translator.translation_config = SimpleNamespace(
            disable_rich_text_translate=False, min_text_length=3, raise_if_cancelled=lambda: None,
        )
        translator.support_llm_translate = True
        seen = []
        def prepare(p, font_map, disable):
            seen.append(font_map["F1"])
            return ILTranslator.TranslateInput(p.unicode, [], p.pdf_style)
        translator.get_translate_input = Mock(side_effect=prepare)
        translator.prepare_body_input_plan(docs)
        self.assertIs(seen[0], fonts[0])
        self.assertIs(seen[1], fonts[1])
        tracker = ParagraphTranslateTracker()
        text, ti = translator.pre_translate_paragraph(ps[1], tracker, {"F1": fonts[0]}, {})
        self.assertEqual(text, "A core idea follows.")
        self.assertEqual(translator.get_translate_input.call_count, 2)
        self.assertEqual(translator._body_prepared_inputs[id(ps[1])].unicode, "core idea follows.")
        self.assertTrue(ti.continuation_risk)

    def test_preparation_failure_stays_local_and_cancellation_propagates(self):
        docs, ps, _ = plan_for("First paragraph.", "Second paragraph.")
        translator = ILTranslator.__new__(ILTranslator)
        translator.support_llm_translate = True
        translator.translation_config = SimpleNamespace(
            disable_rich_text_translate=False, min_text_length=3, raise_if_cancelled=Mock(),
        )
        translator.get_translate_input = Mock(side_effect=[
            KeyError("missing_font"), ILTranslator.TranslateInput(ps[1].unicode, []),
        ])
        translator.prepare_body_input_plan(docs)
        self.assertIsNone(translator.body_input_plan.get(ps[0]))
        self.assertIsNotNone(translator.body_input_plan.get(ps[1]))
        import asyncio
        translator.translation_config.raise_if_cancelled.side_effect = asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            translator.prepare_body_input_plan(docs)

    def test_batch_fallback_and_restart_use_identical_repaired_input(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "paragraph-recovery.sqlite3"
            for attempt in range(2):
                docs, ps, _ = plan_for("The experiment finished. A", "core idea follows.")
                recovery = TranslationRecovery(path, "body-input-v1-test")
                self.addCleanup(recovery.close)
                config = SimpleNamespace(
                    recovery=recovery, skip_references=False, min_text_length=3,
                    disable_same_text_fallback=False, disable_rich_text_translate=False,
                    add_formula_placehold_hint=False, raise_if_cancelled=lambda: None,
                )
                recovery.register(docs, config)
                requests = []
                def respond(text, **kwargs):
                    requests.append(text)
                    if text.startswith("["):
                        rows = json.loads(text)
                        self.assertEqual(rows[1]["input"], "A core idea follows.")
                        self.assertIn("read_only_context", rows[1])
                        return json.dumps([{"id": 0, "output": "实验结束了。"},
                                           {"id": 1, "output": ""}])
                    self.assertEqual(text, "A core idea follows.")
                    return "随后提出了一个核心思想。"
                engine = SimpleNamespace(lang_out="zh-CN", llm_translate=Mock(side_effect=respond))
                single = ILTranslator.__new__(ILTranslator)
                single.translation_config = config
                single.translate_engine = engine
                single.support_llm_translate = True
                single.use_as_fallback = True
                single.get_translate_input = lambda p, *args: ILTranslator.TranslateInput(p.unicode, [], p.pdf_style)
                single.generate_prompt_for_llm = lambda text, *args: text
                single.prepare_body_input_plan(docs)
                batch = ILTranslatorLLMOnly.__new__(ILTranslatorLLMOnly)
                batch.translation_config = config
                batch.translate_engine = engine
                batch.il_translator = single
                batch._build_llm_prompt = lambda **kw: kw["json_input_str"]
                batch.calc_token_count = len
                batch.total_count = batch.fallback_count = batch.ok_count = 0
                def submit(fn, *args, **kwargs):
                    kwargs.pop("priority", None)
                    return fn(*args, **kwargs)
                batch.translate_paragraph(
                    BatchParagraph(ps, [docs.page[0]] * 2, PageTranslateTracker()),
                    Mock(), {}, {}, executor=SimpleNamespace(submit=submit),
                )
                single.finish_translation_completion()
                recovery.finish()
                self.assertEqual(recovery.snapshot()[0]["succeeded"], 2)
                self.assertEqual(len(requests), 2 if attempt == 0 else 0)
                self.assertEqual([p.unicode for p in ps], ["实验结束了。", "随后提出了一个核心思想。"])
                recovery.close()


class GeometrySpacingTests(unittest.TestCase):
    @staticmethod
    def char(text, x, y=10, size=10):
        return PdfCharacter(char_unicode=text, box=Box(x=x, x2=x+4, y=y, y2=y+8),
                            pdf_style=PdfStyle(font_id="F1", font_size=size), pdf_character_id=1)

    def test_larger_gap_elsewhere_does_not_hide_word_space(self):
        chars = [self.char(char, i * 4.2) for i, char in enumerate("egocentric")]
        x = chars[-1].box.x2 + 2.5
        chars.extend(self.char(char, x + i * 4.2) for i, char in enumerate("formulation"))
        chars.append(self.char("works", chars[-1].box.x2 + 10))
        self.assertEqual(get_char_unicode_string(chars, body_input=True), "egocentric formulation works")

    def test_style_boundaries_preserve_local_spacing_without_touching_tokens(self):
        chars = [self.char("egocentric", 0), "<style id='1'>", self.char("formulation", 7), "</style>"]
        self.assertEqual(get_char_unicode_string(chars, body_input=True), "egocentric<style id='1'> formulation</style>")
        self.assertEqual(get_char_unicode_string([self.char("x", 0), "{v1}", self.char("y", 20)], body_input=True), "x{v1}y")

    def test_kerning_scale_newlines_and_vertical_text(self):
        self.assertEqual(get_char_unicode_string([self.char("a", 0), self.char("b", 5)], body_input=True), "ab")
        self.assertEqual(get_char_unicode_string([self.char("word", 0), self.char("next", 0, y=-5)], body_input=True), "word\nnext")
        a, b = self.char("a", 0), self.char("b", 20)
        a.vertical = b.vertical = True
        self.assertEqual(get_char_unicode_string([a, b], body_input=True), "ab")

    def test_explicit_space_advance_and_same_font_size_are_required(self):
        space = self.char(" ", -10)
        space.advance = 2
        chars = [space, self.char("a", 0), self.char("b", 5.5)]
        self.assertEqual(get_char_unicode_string(chars, body_input=True), "a b")
        chars[-1].pdf_style.font_id = "F2"
        self.assertEqual(get_char_unicode_string(chars, body_input=True), "ab")
        chars[-1].pdf_style.font_id = "F1"
        chars[-1].pdf_style.font_size = 8
        self.assertEqual(get_char_unicode_string(chars, body_input=True), "ab")
        self.assertEqual(get_char_unicode_string([self.char("中", 0), self.char("文", 20)], body_input=True), "中文")

    def test_default_extraction_keeps_legacy_spacing_and_folds_newlines(self):
        self.assertEqual(get_char_unicode_string([self.char("a", 0), self.char("b", 5)]), "a b")
        self.assertEqual(get_char_unicode_string([self.char("word", 0), self.char("next", 0, y=-5)]), "word next")

    def test_actual_single_line_body_input_preserves_source_and_line_origin(self):
        chars = [self.char("resid-", 0), self.char("ual", 0, y=-5)]
        p = paragraph("resid- ual")
        p.pdf_paragraph_composition = [PdfParagraphComposition(pdf_line=PdfLine(pdf_character=chars))]
        translator = ILTranslator.__new__(ILTranslator)
        translator._formula_placeholder_pattern = re.compile(r"\{v\d+\}")
        translator._style_left_placeholder_pattern = re.compile(r"<style[^>]*>")
        translator._style_right_placeholder_pattern = re.compile(r"</style>")
        ti = translator.get_translate_input(p)
        self.assertEqual(ti.unicode, "resid-\nual")
        self.assertEqual(p.unicode, "resid- ual")


class AccentRegressionTests(unittest.TestCase):
    def test_plucker_and_prose_diacritics_survive_normalization(self):
        for spelling in ("Plücker", "Plu\u0308cker"):
            source = f"{spelling} coordinates and naïve cafés use cam-\nera observations."
            chars = [GeometrySpacingTests.char(char, index * 4.2) for index, char in enumerate(source)]
            extracted = get_char_unicode_string(chars, body_input=True)
            docs, ps, _ = plan_for(extracted)
            plan = BodyInputPlan(docs, {id(ps[0]): extracted}, evidence=["camera"])
            self.assertEqual(unicodedata.normalize("NFC", plan.get(ps[0]).text),
                             "Plücker coordinates and naïve cafés use camera observations.")
            self.assertEqual(ps[0].unicode, extracted)
        mapper = SimpleNamespace(has_char=lambda char: True)
        config = SimpleNamespace(formular_char_pattern=None)
        self.assertFalse(is_formulas_start_char("ü", mapper, config))
        # Existing classification still protects a separately emitted diaeresis.
        # This does not claim that PDF extraction always produces a whole ü glyph.
        self.assertTrue(is_formulas_start_char("\u0308", mapper, config))
        self.assertTrue(is_formulas_start_char("¨", mapper, config))

    def test_math_accent_stays_in_its_original_formula_through_placeholder_roundtrip(self):
        mapper = SimpleNamespace(has_char=lambda char: True)
        self.assertTrue(is_formulas_start_char("\u0302", mapper, SimpleNamespace(formular_char_pattern=None)))
        base = GeometrySpacingTests.char("x", 40)
        accent = GeometrySpacingTests.char("\u0302", 40, y=18)
        formula = PdfFormula(pdf_character=[base, accent], box=Box(x=40, x2=44, y=10, y2=26))
        original_formula = copy.deepcopy(formula)
        p = paragraph("We use x\u0302 with Plücker coordinates.")
        p.pdf_paragraph_composition = [
            PdfParagraphComposition(pdf_line=PdfLine(pdf_character=[GeometrySpacingTests.char("We use ", 0)])),
            PdfParagraphComposition(pdf_formula=formula),
            PdfParagraphComposition(pdf_line=PdfLine(pdf_character=[GeometrySpacingTests.char(" with Plücker coordinates.", 50)])),
        ]
        translator = ILTranslator.__new__(ILTranslator)
        translator.translation_config = SimpleNamespace(disable_rich_text_translate=False)
        translator.translate_engine = SimpleNamespace(get_formular_placeholder=lambda index: (
            "{v" + str(index) + "}", re.escape("{v" + str(index) + "}"),
        ))
        translator._formula_placeholder_pattern = re.compile(r"\{v\d+\}")
        translator._style_left_placeholder_pattern = re.compile(r"<style[^>]*>")
        translator._style_right_placeholder_pattern = re.compile(r"</style>")
        ti = translator.get_translate_input(p)
        self.assertEqual(ti.unicode, "We use {v1} with Plücker coordinates.")
        docs = Document(page=[Page(page_number=0, pdf_paragraph=[p])])
        BodyInputPlan(docs, {id(p): ti.unicode}).apply(p, ti)
        output = "我们结合 Plücker 坐标使用{v1}。"
        validate_text(ti.unicode, output, "zh-CN")
        with self.assertRaises(InvalidTranslation):
            validate_text(ti.unicode, "我们结合 Plücker 坐标使用 x。", "zh-CN")
        compositions = translator.parse_translate_output(ti, output, ParagraphTranslateTracker(), None)
        restored = [comp.pdf_formula for comp in compositions if comp.pdf_formula is not None]
        self.assertEqual(len(restored), 1)
        self.assertIs(restored[0], formula)
        self.assertEqual(formula, original_formula)
