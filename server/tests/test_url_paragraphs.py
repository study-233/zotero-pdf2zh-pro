import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from babeldoc.format.pdf.document_il import (
    Box, Document, Page, PdfCharacter, PdfLine, PdfParagraph,
    PdfParagraphComposition, PdfStyle,
)
from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator, ParagraphTranslateTracker
from babeldoc.format.pdf.document_il.midend.il_translator_llm_only import ILTranslatorLLMOnly, BatchParagraph
from babeldoc.format.pdf.document_il.midend.il_translator import PageTranslateTracker
from babeldoc.format.pdf.document_il.utils.paragraph_helper import is_url_only_paragraph
from babeldoc.format.pdf.translation_recovery import TranslationRecovery
from babeldoc.translator.url_utils import is_url_only_text
from babeldoc.translator.validation import InvalidTranslation, validate_text, validate_batch


APPLE = '1https://support.apple.com/en-gb/guide/apple-vision-pro/dev26039f68/'
URLS = [
    APPLE + ' visionos',
    '2https://nodered.org 3https://mosquitto.org 4https://nodejs.org 5https://couchdb.apache.org/ 6https://mqtt.org',
    '7https://project-chip.github.io/connectedhomeip-doc/',
    '8https://github.com/langchain-ai/langchain',
    '9https://record3d.app/',
]


def paragraph(lines, identifier='one'):
    compositions = []
    for index, text in enumerate(lines):
        y = 100 - index * 10
        chars = [PdfCharacter(char_unicode=char, box=Box(x=i*4, x2=i*4+4, y=y, y2=y+8))
                 for i, char in enumerate(text)]
        compositions.append(PdfParagraphComposition(pdf_line=PdfLine(pdf_character=chars)))
    return PdfParagraph(debug_id=identifier, unicode=' '.join(lines), pdf_style=PdfStyle(),
                        box=Box(x=0, x2=max(map(len, lines))*4, y=80, y2=108),
                        pdf_paragraph_composition=compositions)


class URLParagraphTests(unittest.TestCase):
    def test_five_actual_sources_and_rich_text(self):
        for i, source in enumerate(URLS):
            p = paragraph([APPLE, 'visionos'] if i == 0 else [source])
            self.assertTrue(is_url_only_paragraph(p), source)
        rich = "{v1}<style id='2'>https://nodered.org </style>{v4}<style id='5'>https://mosquitto.org</style>"
        for source in [*URLS[1:], rich, '[12] https://example.org/path?q=one#section']:
            self.assertTrue(is_url_only_text(source))
            validate_text(source, source, 'zh-CN')
            validate_batch(json.dumps([{'id':0,'output':source}]), [source], 'zh-CN')
        # A flattened string alone cannot establish a PDF wrap.
        self.assertFalse(is_url_only_text(URLS[0]))

    def test_wrap_needs_geometry_not_whitespace_removal(self):
        self.assertFalse(is_url_only_paragraph(paragraph([APPLE + ' visionos'])))
        self.assertFalse(is_url_only_paragraph(paragraph([APPLE, 'This is normal prose.'])))
        self.assertFalse(is_url_only_paragraph(paragraph([APPLE, 'Introduction'])))
        self.assertFalse(is_url_only_paragraph(paragraph([APPLE, 'more information'])))
        p = paragraph([APPLE, 'visionos'])
        p.box.x2 += 50  # Not a wrap at the right edge.
        self.assertFalse(is_url_only_paragraph(p))
        p = paragraph([APPLE, 'visionos'])
        for char in p.pdf_paragraph_composition[1].pdf_line.pdf_character:
            char.box.y -= 60
            char.box.y2 -= 60
        self.assertFalse(is_url_only_paragraph(p))

    def test_mixed_prose_and_structural_validation_remain_strict(self):
        for source in ['See https://record3d.app/ for more information.',
                       'https://example.org/ provided by the authors.',
                       'This is a complete untranslated paragraph about experiments.', 'Introduction']:
            self.assertFalse(is_url_only_paragraph(paragraph([source])))
            with self.assertRaises(InvalidTranslation):
                validate_text(source, source, 'zh-CN')
        for output in ['', 'https://example.org/']:
            with self.assertRaises(InvalidTranslation):
                validate_text('{v1}https://example.org/', output, 'zh-CN')
        validate_text('See https://example.org/ for more information.',
                      '更多信息请参见 https://example.org/。', 'zh-CN')

    def test_historical_failure_reclassified_and_restart_reuses_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = SimpleNamespace(skip_references=False, min_text_length=3,
                                  disable_same_text_fallback=False, raise_if_cancelled=lambda:None)
            path = Path(tmp)/'paragraph-recovery.sqlite3'
            prose = 'This is a complete English paragraph about the experiment.'
            target = '这是一个关于实验的完整中文段落。'
            def documents():
                return Document(page=[Page(page_number=1, pdf_paragraph=[
                    paragraph([APPLE,'visionos']), paragraph([prose], 'two')])])
            docs = documents()
            recovery = TranslationRecovery(path, 'same-source-config')
            recovery.register(docs, cfg)
            recovery.fail(docs.page[0].pdf_paragraph[0], InvalidTranslation('unchanged_translation'))
            recovery.record(docs.page[0].pdf_paragraph[1], 'succeeded', input=prose, translation=target)
            recovery.finish()
            recovery.close()
            for _ in range(2):
                docs = documents()
                recovery = TranslationRecovery(path, 'same-source-config')
                cfg.recovery = recovery
                recovery.register(docs, cfg)
                translator = ILTranslator.__new__(ILTranslator)
                translator.translation_config = cfg
                translator.translate_engine = SimpleNamespace(lang_out='zh-CN', llm_translate=Mock())
                translator._prepare_paragraph = lambda p,*args: (p.unicode, SimpleNamespace(unicode=p.unicode))
                def restore(p, tracker, ti, output):
                    validate_text(ti.unicode, output, 'zh-CN')
                    recovery.record(p, 'succeeded', input=ti.unicode, translation=output)
                    p.unicode = output
                translator.post_translate_paragraph = restore
                for p in docs.page[0].pdf_paragraph:
                    self.assertEqual(translator.pre_translate_paragraph(p, Mock(), {}, {}), (None, None))
                recovery.finish()
                translator.translate_engine.llm_translate.assert_not_called()
                self.assertEqual(recovery.snapshot()[0], dict(total=2,succeeded=1,skipped=1,failed=0,pending=0))
                self.assertEqual(docs.page[0].pdf_paragraph[0].unicode, URLS[0])
                self.assertEqual(docs.page[0].pdf_paragraph[1].unicode, target)
                entries=recovery.entries.values()
                url=next(e for e in entries if e['paragraphId']=='one')
                self.assertEqual((url['reason'],url['attempts']),('url_only',0))
                recovery.close()

    def test_batch_and_single_skip_without_recovery(self):
        cfg = SimpleNamespace(raise_if_cancelled=lambda:None)
        translator = ILTranslator.__new__(ILTranslator)
        translator.translation_config = cfg
        translator.use_as_fallback = False
        translator.translate_engine = Mock()
        translator._prepare_paragraph = Mock(side_effect=AssertionError('must skip before preparing API input'))
        page=Page(page_number=0, pdf_paragraph=[paragraph([APPLE,'visionos'])])
        p=page.pdf_paragraph[0]
        translator.translate_paragraph(p,page,Mock(),ParagraphTranslateTracker(),{}, {})
        batch=ILTranslatorLLMOnly.__new__(ILTranslatorLLMOnly)
        batch.translation_config=cfg
        batch.il_translator=translator
        batch.translate_engine=Mock()
        batch.translate_paragraph(BatchParagraph([p],[page],PageTranslateTracker()),Mock(),{}, {})
        translator._prepare_paragraph.assert_not_called()
        translator.translate_engine.llm_translate.assert_not_called()
        batch.translate_engine.llm_translate.assert_not_called()


if __name__ == '__main__':
    unittest.main()
