"""Exercise the real native parser, paragraph processing, typesetting and renderer.

Only external layout inference, model requests and downloadable fonts are replaced.
The PDF fixture and embedded Chinese font are generated locally by PyMuPDF.
"""
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pymupdf

from babeldoc.docvision.base_doclayout import YoloBox, YoloResult
from babeldoc.format.pdf.high_level import translate
from babeldoc.format.pdf.translation_config import TranslationConfig, WatermarkOutputMode
from babeldoc.format.pdf.translation_recovery import TranslationRecovery
from pdf2zh_pro.config import create_model_settings
from pdf2zh_pro.translator.base import BaseTranslator
from pdf2zh_pro.translator.rate_limit import QPSRateLimiter


class FixtureTranslator(BaseTranslator):
    name = 'native-fixture'

    def __init__(self):
        super().__init__(create_model_settings({'service': 'openai', 'llm_api': {'apiKey': 'fixture'}}), QPSRateLimiter(100))
        self.ignore_cache = True
        self.requests = []

    def do_translate(self, text, rate_limit_params=None):
        self.requests.append(text)
        return '论文的翻译结果。'


class FixtureLayout:
    def handle_document(self, pages, document, config, save_debug_image):
        for page in pages:
            rect = document[page.page_number].rect
            yield page, YoloResult(names={0: 'plain text'}, boxes=[YoloBox(
                xyxy=np.array([0, 0, rect.width, rect.height]), conf=np.float32(1), cls=0)])


class NativePipelineTests(unittest.TestCase):
    def test_native_translation_rendering_page_filter_ocr_and_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'input.pdf'
            with pymupdf.open() as document:
                document.new_page().insert_text((72, 72), 'This paper presents a novel translation method.')
                document.new_page().insert_text((72, 72), 'This page is excluded from translation.')
                document.save(source)
            font = pymupdf.Font('china-s')
            font_path = root / 'fixture.ttf'
            font_path.write_bytes(font.buffer)
            font_metadata = {'ascent': font.ascender * 1000, 'descent': font.descender * 1000, 'encoding_length': 2}
            families = {key: ['fixture.ttf'] for key in ('normal', 'script', 'fallback', 'base')}
            with patch('babeldoc.assets.assets.get_font_family', return_value=families), patch(
                'babeldoc.assets.assets.get_font_and_metadata', return_value=(font_path, font_metadata)
            ):
                for attempt in (1, 2):
                    translator = FixtureTranslator()
                    watermark = WatermarkOutputMode.Both if attempt == 1 else WatermarkOutputMode.NoWatermark
                    config = TranslationConfig(translator, source, 'en', 'zh-CN', FixtureLayout(),
                        working_dir=root / f'work-{attempt}', output_dir=root / f'out-{attempt}',
                        pages='1', only_include_translated_page=True, auto_extract_glossary=False,
                        ocr_workaround=True, pool_max_workers=1, use_rich_pbar=False,
                        watermark_output_mode=watermark)
                    config.recovery = TranslationRecovery(root / 'recovery.json', 'native-pipeline-fixture')
                    try:
                        with ExitStack() as checks:
                            if attempt == 2:
                                for target in (
                                    'babeldoc.format.pdf.new_parser.native_parse.parse_prepared_pdf_with_new_parser_to_legacy_ir',
                                    'babeldoc.format.pdf.high_level.LayoutParser.process',
                                    'babeldoc.format.pdf.high_level.ParagraphFinder.process',
                                    'babeldoc.format.pdf.high_level.StylesAndFormulas.process',
                                ):
                                    checks.enter_context(patch(target, side_effect=AssertionError('Repair must reuse preprocessing')))
                            result = translate(config)
                        self.assertEqual(config.watermark_output_mode, watermark, 'watermark generation must not silently fall back')
                        if watermark == WatermarkOutputMode.Both:
                            self.assertNotEqual(result.mono_pdf_path, result.no_watermark_mono_pdf_path)
                            self.assertTrue(result.no_watermark_mono_pdf_path.is_file())
                        if attempt == 1:
                            self.assertGreater(len(translator.requests), 0)
                        else:
                            self.assertEqual(translator.requests, [], 'recovery must reuse completed paragraphs')
                        self.assertEqual(config.recovery.snapshot()[0]['failed'], 0)
                        for path in (result.mono_pdf_path, result.dual_pdf_path):
                            with pymupdf.open(path) as document:
                                self.assertEqual(len(document), 1)
                                self.assertIn('论文的翻译结果', ''.join(page.get_text() for page in document))
                                self.assertGreater(len(set(document[0].get_pixmap().samples)), 1)
                    finally:
                        config.recovery.close()
