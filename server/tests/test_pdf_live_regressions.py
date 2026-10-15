"""Regressions found by the fixed Codex PDF smoke pages, without model calls."""
import copy
import re
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import pymupdf
import babeldoc.format.pdf.high_level  # Initialize the existing extract_char/high_level import cycle.
from babeldoc.format.pdf.document_il import (
    Box, Page, PageLayout, PdfCharacter, PdfFormula, PdfLine, PdfParagraph,
    PdfParagraphComposition, PdfSameStyleCharacters, PdfStyle, VisualBbox,
)
from babeldoc.format.pdf.document_il.midend.il_translator import (
    FormulaPlaceholder, ILTranslator, ParagraphTranslateTracker,
)
from babeldoc.format.pdf.document_il.midend.layout_parser import LayoutParser
from babeldoc.format.pdf.document_il.midend.paragraph_finder import ParagraphFinder
from babeldoc.format.pdf.document_il.midend.typesetting import Typesetting, TypesettingUnit
from babeldoc.format.pdf.document_il.utils.layout_helper import (
    build_layout_index, get_character_layout, get_paragraph_unicode,
)


def character(text, x=0, y=0):
    return PdfCharacter(char_unicode=text, box=Box(x, y, x+5, y+10),
                        visual_bbox=VisualBbox(Box(x, y, x+5, y+10)))


class PDFLiveRegressionTests(unittest.TestCase):
    def translator(self):
        value = ILTranslator.__new__(ILTranslator)
        value._formula_placeholder_pattern = re.compile(r"\{v\d+\}")
        value._style_left_placeholder_pattern = re.compile(r"<style id=['\"]\d+['\"]>")
        value._style_right_placeholder_pattern = re.compile(r"</style>")
        return value

    def test_native_ruled_cells_group_lines_without_crossing_rows_or_columns(self):
        with pymupdf.open() as doc:
            pdf = doc.new_page(width=300, height=300)
            for x in (20, 150, 280):
                pdf.draw_line((x, 20), (x, 160))
            for y in (20, 90, 160):
                pdf.draw_line((20, y), (280, y))
            pdf.insert_text((25, 40), 'First line')
            pdf.insert_text((25, 55), 'same cell')
            pdf.insert_text((155, 40), 'Other column')
            pdf.insert_text((25, 110), 'Other row')
            page = Page(page_layout=[PageLayout(id=7, box=Box(20, 140, 280, 280), class_name='table')])
            parser = LayoutParser(SimpleNamespace(doc_layout_model=None, translate_table_text=True))
            parser.add_native_table_cells(page, pdf)
            self.assertEqual(len(page.page_layout), 5)
            self.assertEqual(len({p.id for p in page.page_layout}), 5)
            index, mapping = build_layout_index(page)
            def cell(x, y):
                return get_character_layout(character('A', x, 300-y), index, mapping).id
            self.assertEqual(cell(30, 40), cell(30, 55))
            self.assertNotEqual(cell(30, 40), cell(160, 40))
            self.assertNotEqual(cell(30, 40), cell(30, 110))
            parser.translation_config.translate_table_text = False
            disabled = Page(page_layout=page.page_layout[:1])
            parser.add_native_table_cells(disabled, pdf)
            self.assertEqual(len(disabled.page_layout), 1)

    def test_short_lines_inside_native_cell_remain_one_translation_unit(self):
        finder = ParagraphFinder.__new__(ParagraphFinder)
        finder.translation_config = SimpleNamespace(split_short_lines=True, short_line_split_factor=.5)
        paragraph = PdfParagraph(layout_label='wired_table_cell', pdf_paragraph_composition=[
            PdfParagraphComposition(pdf_line=PdfLine(box=Box(0, y, 10, y+10), pdf_character=[character('text', 0, y)]))
            for y in (20, 0)])
        paragraphs = [paragraph]
        finder.process_independent_paragraphs(paragraphs, 100)
        self.assertEqual(len(paragraphs), 1)
        self.assertEqual(len(paragraphs[0].pdf_paragraph_composition), 2)

    def test_styled_spaces_do_not_create_required_tags_or_mutate_the_source(self):
        translator = self.translator()
        translator.translation_config = SimpleNamespace(disable_rich_text_translate=False)
        paragraph = PdfParagraph(unicode='Time services.', pdf_style=PdfStyle(), pdf_paragraph_composition=[
            PdfParagraphComposition(pdf_character=character('Time')),
            PdfParagraphComposition(pdf_same_style_characters=PdfSameStyleCharacters(
                pdf_style=PdfStyle(font_id='unavailable-space-font'), pdf_character=[character(' ', 6)])),
            PdfParagraphComposition(pdf_character=character('services.', 12)),
        ])
        original = copy.deepcopy(paragraph)
        result = translator.get_translate_input(paragraph, {})
        self.assertEqual(result.unicode, 'Time services.')
        self.assertEqual(result.placeholders, [])
        self.assertEqual(paragraph, original)

    def test_citation_boundary_cleanup_preserves_formula_and_recovery_text(self):
        translator = self.translator()
        recovery = Mock()
        translator.translation_config = SimpleNamespace(raise_if_cancelled=lambda: None,
            disable_same_text_fallback=False, recovery=recovery)
        translator.translate_engine = SimpleNamespace(lang_out='zh-CN')
        for original, target, expected in [
            ('[8],', 'GFS {v1}、和服务。', 'GFS {v1}和服务。'),
            ('[26], [39],', '如 {v1}，但不同。', '如 {v1}但不同。'),
            ('[8]', 'GFS {v1}，以及服务。', 'GFS {v1}，以及服务。'),
            ('x,y', '公式 {v1}，成立。', '公式 {v1}，成立。'),
            ('[8],', '引用 {v1}；对比 {v99}，保留。', '引用 {v1}；对比 {v99}，保留。'),
        ]:
            with self.subTest(original=original, target=target):
                formula = PdfFormula(pdf_character=[character(original)])
                value = FormulaPlaceholder(1, formula, '{v1}', re.escape('{v1}'))
                source = 'Citation {v1} and text.' + (' {v99}' if '{v99}' in target else '')
                prepared = ILTranslator.TranslateInput(source, [value], PdfStyle())
                prepared.set_original_placeholder_tokens({'{v99}': 1} if '{v99}' in target else {})
                paragraph = PdfParagraph(pdf_style=PdfStyle())
                translator.post_translate_paragraph(paragraph, ParagraphTranslateTracker(), prepared, target)
                self.assertEqual(paragraph.unicode, expected)
                self.assertEqual(recovery.record.call_args.kwargs['translation'], expected)
                self.assertIn(original, get_paragraph_unicode(paragraph))
                self.assertIs(paragraph.pdf_paragraph_composition[1].pdf_formula, formula)

    def test_styled_phrase_keeps_one_span_and_cannot_merge_across_formulas(self):
        bold, plain = PdfStyle(font_id='bold', font_size=10), PdfStyle(font_id='plain', font_size=10)
        def styled(text, style):
            return PdfParagraphComposition(pdf_same_style_characters=PdfSameStyleCharacters(
                pdf_style=style, pdf_character=[character(text)]))
        formula = PdfParagraphComposition(pdf_formula=PdfFormula(pdf_character=[character('x')]))
        source = [styled('Time', bold), styled(' ', plain), styled('services', bold),
                  formula, styled('based on context.', bold)]
        original = copy.deepcopy(source)
        result = ILTranslator.coalesce_styled_words(source)
        self.assertEqual(len(result), 3)
        self.assertEqual(''.join(c.char_unicode for c in result[0].pdf_same_style_characters.pdf_character), 'Time services')
        self.assertIs(result[1], formula)
        self.assertEqual(source, original)

    def test_ruled_cell_keeps_closing_punctuation_inside_its_border(self):
        typesetter = Typesetting.__new__(Typesetting)
        font = pymupdf.Font(fontname='china-s')
        typesetter.font_mapper = SimpleNamespace(base_font=font)
        typesetter.is_cjk = True
        units = [TypesettingUnit(unicode=c, font=font, font_size=10,
                    style=PdfStyle(font_id='base', font_size=10), xobj_id=0)
                 for c in '测试文本。”']
        paragraph = PdfParagraph(layout_label='wired_table_cell', layout_id=7,
            box=Box(2, 35, 42, 48), debug_id='cell')
        page = Page(page_layout=[PageLayout(id=7, class_name='wired_table_cell',
                                            box=Box(0, 0, 44, 50))])
        scale, result = typesetter._find_optimal_scale_and_layout(
            paragraph, page, units, apply_layout=True)
        self.assertEqual(scale, 1)
        self.assertTrue(result)
        self.assertTrue(all(0 < unit.box.x < unit.box.x2 < 44 and unit.box.y > 0
                            for unit in result))
        self.assertEqual(result[-1].box.y, result[-2].box.y)
        self.assertLess(result[-1].box.y, result[0].box.y)
        body = PdfParagraph(layout_label='text')
        original, fits = typesetter._layout_typesetting_units(
            units, Box(2, 0, 42, 48), 1, 1.5, body)
        self.assertTrue(fits)
        self.assertGreater(original[-1].box.x2, 44)
