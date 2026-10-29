import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf

import babeldoc.format.pdf.high_level  # Initialize the existing parser import cycle.
from babeldoc.format.pdf.document_il import il_version_1 as il
from babeldoc.format.pdf.document_il.frontend.il_creater_active_support import (
    LazyPassthroughInstruction, PassthroughSnapshot,
)
from babeldoc.format.pdf.preprocessing_cache import PreprocessingCache
from babeldoc.format.pdf.translation_config import TranslationConfig
from babeldoc.format.pdf.translation_recovery import TranslationRecovery
from diagnostics import DiagnosticStore


class PreprocessingCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source.pdf'
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((72, 72), 'Original document')
            pdf.save(self.source)
        self.config = TranslationConfig(None, self.source, 'en', 'zh-CN', None,
            working_dir=self.root/'work', output_dir=self.root/'out')
        self.config.recovery = TranslationRecovery(self.root/'recovery.sqlite3', 'fixture')
        self.addCleanup(self.config.recovery.close)
        self.store = DiagnosticStore()
        self.config.diagnostic_callback = lambda event: self.store.record('parse_detail', event)
        self.cache = PreprocessingCache(self.config)
        self.document = il.Document(total_pages=1, page=[il.Page(page_number=0,
            pdf_paragraph=[il.PdfParagraph(unicode='Original document',
                pdf_style=il.PdfStyle(graphic_state=il.GraphicState(
                    passthrough_per_char_instruction=LazyPassthroughInstruction(PassthroughSnapshot(), suffix_parts=['0 g'])
                )))])])

    def save(self):
        self.cache.save(self.document, self.source, {3: {'CropBox': '[0 0 400 500]'}})

    def test_checkpoint_is_before_translation_and_preserves_graphics_and_ocr(self):
        self.config.ocr_workaround = True
        self.config.shared_context_cross_split_part.auto_enabled_ocr_workaround = True
        self.config.shared_context_cross_split_part.add_valid_counts(200, 60)
        self.save()
        self.document.page[0].pdf_paragraph[0].unicode = 'Translated later'
        self.config.ocr_workaround = False
        result = self.cache.load(self.root/'restored.pdf')
        self.assertIsNotNone(result)
        document, pdf, boxes = result
        self.addCleanup(pdf.close)
        paragraph = document.page[0].pdf_paragraph[0]
        self.assertEqual(paragraph.unicode, 'Original document')
        self.assertEqual(paragraph.pdf_style.graphic_state.passthrough_per_char_instruction, '0 g')
        self.assertEqual(boxes, {3: {'CropBox': '[0 0 400 500]'}})
        self.assertTrue(self.config.ocr_workaround)
        self.assertTrue(self.config.shared_context_cross_split_part.auto_enabled_ocr_workaround)
        self.assertEqual(self.config.shared_context_cross_split_part.valid_char_count_total, 400)
        self.assertEqual(self.config.shared_context_cross_split_part.total_valid_text_token_count, 120)
        self.assertIn('preprocessing_cache_hit', json.dumps(self.store.export()))

    def test_settings_source_and_engine_version_invalidate_checkpoint(self):
        self.save()
        for field, value in [('pages', '1'), ('page_ranges', [(1, 1)]), ('translate_table_text', True), ('lang_out', 'ja')]:
            original = getattr(self.config, field)
            setattr(self.config, field, value)
            self.assertIsNone(PreprocessingCache(self.config).load(self.root/'restored.pdf'))
            setattr(self.config, field, original)
        with patch('babeldoc.format.pdf.preprocessing_cache.VERSION', 999):
            self.assertIsNone(PreprocessingCache(self.config).load(self.root/'restored.pdf'))
        self.config.recovery.fingerprint = 'different provider or parse settings'
        self.assertIsNone(PreprocessingCache(self.config).load(self.root/'restored.pdf'))
        self.config.recovery.fingerprint = 'fixture'
        self.source.write_bytes(self.source.read_bytes() + b'\n% changed')
        self.assertIsNone(PreprocessingCache(self.config).load(self.root/'restored.pdf'))

    def test_corruption_and_write_failure_fall_back_and_export_without_private_data(self):
        self.save()
        checkpoint = self.cache.path.read_bytes()
        with patch('babeldoc.format.pdf.preprocessing_cache.ZipFile.writestr', side_effect=OSError('private path sk-secret')):
            self.save()
        self.assertEqual(self.cache.path.read_bytes(), checkpoint)
        self.cache.path.write_bytes(b'broken archive')
        self.assertIsNone(self.cache.load(self.root/'restored.pdf'))
        exported = json.dumps(self.store.export())
        self.assertIn('preprocessing_cache_invalid', exported)
        self.assertIn('preprocessing_cache_write_failed', exported)
        self.assertNotIn('private path', exported)
        self.assertNotIn('sk-secret', exported)
        self.assertFalse(self.cache.path.with_suffix('.tmp').exists())

    def test_cancellation_preserves_previous_checkpoint(self):
        self.save()
        checkpoint = self.cache.path.read_bytes()
        with patch.object(self.config, 'raise_if_cancelled', side_effect=[None, asyncio.CancelledError]):
            with self.assertRaises(asyncio.CancelledError):
                self.save()
        self.assertEqual(self.cache.path.read_bytes(), checkpoint)
        self.assertFalse(self.cache.path.with_suffix('.tmp').exists())

    def test_layout_model_is_lazy_and_debug_bypasses_checkpoint(self):
        self.assertIsNone(self.config.doc_layout_model)
        self.config.debug = True
        self.assertIsNone(PreprocessingCache(self.config).path)
        self.config.debug = False
        self.config.only_parse_generate_pdf = True
        self.assertIsNone(PreprocessingCache(self.config).path)

    def test_split_parts_share_lazy_model_and_fonts(self):
        import copy
        from babeldoc.format.pdf.document_il.midend.layout_parser import LayoutParser
        first, second = copy.copy(self.config), copy.copy(self.config)
        with patch('babeldoc.docvision.base_doclayout.DocLayoutModel.load_available', return_value=object()) as load:
            a, b = LayoutParser(first), LayoutParser(second)
            self.assertIs(a.model, b.model)
        load.assert_called_once()
        self.assertIs(first._font_resource_cache, second._font_resource_cache)

    def test_split_part_identity_ignores_generated_pdf_ids_but_binds_source_and_range(self):
        self.config.preprocessing_source_key = ['original-source-hash', 0, 9]
        first = PreprocessingCache(self.config)
        self.source.write_bytes(self.source.read_bytes() + b'\n% regenerated part ID')
        self.assertEqual(first.identity, PreprocessingCache(self.config).identity)
        for key in (['other-source', 0, 9], ['original-source-hash', 10, 19]):
            self.config.preprocessing_source_key = key
            self.assertNotEqual(first.path, PreprocessingCache(self.config).path)
