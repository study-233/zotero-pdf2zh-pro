import json
import re
import tempfile
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import Mock

from babeldoc.translator.preserved_text import preserved_text_reason
from babeldoc.translator.literal_tags import LiteralTags
from babeldoc.translator.validation import InvalidTranslation, validate_text
from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator, ParagraphTranslateTracker
from babeldoc.format.pdf.document_il.utils.layout_helper import get_paragraph_unicode
from pdf2zh_next.translator.base_translator import BaseTranslator
import test_partial_batch as batch_fixtures


SOURCES = [
    '<image "bg-img.png" />', 'font-family, font-size',
    '.edu.cn, fangsc@szu.edu.cn, qqqyd@mail.ustc.edu.cn, htxie@ustc.edu.c',
    'www fridayfantasy com',
]


class PreservedTextTests(unittest.TestCase):
    def test_actual_sources_and_mixed_prose(self):
        for source in SOURCES:
            self.assertIsNotNone(preserved_text_reason(source))
            validate_text(source, source, 'zh-CN')
        for source in ['Please set font-family and font-size.',
                       'Contact test@example.com for more information.',
                       'Visit www.example.com for more information.',
                       'www fridayfantasy com provides more information.',
                       'This is an untranslated English paragraph.',
                       '<img src="a.png" alt="a blue square" />', '.edu.cn',
                       'state-of-the-art, real-world', 'color', 'width']:
            self.assertIsNone(preserved_text_reason(source), source)
        for source in SOURCES:
            with self.assertRaises(InvalidTranslation):
                validate_text('{v1}' + source, source, 'zh-CN')
            with self.assertRaises(InvalidTranslation):
                validate_text(source, '', 'zh-CN')

    def test_batch_reclassifies_four_failures_and_reuses_valid_translations_without_requests(self):
        with ExitStack() as stack:
            tmp = stack.enter_context(tempfile.TemporaryDirectory())
            ps, recovery, engine, batch, run = batch_fixtures.PartialBatchTests().fixture(stack, tmp, Mock())
            for p, source in zip(ps, SOURCES):
                p.unicode = source
                recovery.fail(p, InvalidTranslation('unchanged_translation'))
            for p in ps[4:]:
                recovery.record(p, 'succeeded', input=p.unicode, translation='这是实验结果。' + re.search(r'\{v\d+\}', p.unicode).group())
            run()
            self.assertEqual(engine.requests, [])
            summary, failed = recovery.snapshot()
            self.assertEqual((summary['skipped'], summary['succeeded'], failed), (4, 2, []))


class LiteralTagTests(unittest.TestCase):
    def test_split_formula_markup_round_trip_and_collision(self):
        for label, target in [('Visual Reflection', '视觉反思'), ('This Layout is Satisfied', '此布局符合要求')]:
            source = '{v1}think{v2}' + label + '{v3}/think{v4} {v5}'
            tags = LiteralTags(source, {'{v1}': '<', '{v2}': '>', '{v3}': '<', '{v4}': '>'})
            self.assertEqual(tags.text, '{v6}' + label + '{v7} {v5}')
            output = tags.text.replace(label, target)
            validate_text(tags.text, output, 'zh-CN')
            self.assertEqual(tags.restore(output), source.replace(label, target))
            for broken in [output.replace('{v6}', ''), output + '{v6}', '{v7}译文{v6} {v5}']:
                with self.assertRaises(InvalidTranslation):
                    tags.restore(broken)

    def test_document_tags_survive_actual_thought_cleanup(self):
        tags = LiteralTags('<think>Visual Reflection</think>')
        output = tags.text.replace('Visual Reflection', '视觉反思')
        cleaned = BaseTranslator._remove_cot_content(None, '<think>model reasoning</think>' + output)
        self.assertEqual(tags.restore(cleaned), '<think>视觉反思</think>')
        rich = LiteralTags("<style id='1'><b>Text</b></style>")
        self.assertTrue(rich.text.startswith("<style id='1'>"))
        self.assertTrue(rich.text.endswith('</style>'))
        self.assertEqual(rich.restore(rich.text), "<style id='1'><b>Text</b></style>")

    def test_batch_fallback_cache_and_layout_share_protection(self):
        requests = []
        def respond(text):
            requests.append(text)
            def translate(source):
                return source.replace('Visual Reflection', '视觉反思').replace('This Layout is Satisfied', '此布局符合要求')
            if text.startswith('['):
                return json.dumps([{'id': row['id'], 'output': '坏译文' if row['id'] == 1 else translate(row['input'])}
                                   for row in json.loads(text)])
            return translate(text)
        with ExitStack() as stack:
            tmp = stack.enter_context(tempfile.TemporaryDirectory())
            ps, recovery, engine, batch, run = batch_fixtures.PartialBatchTests().fixture(stack, tmp, respond)
            for p, source in zip(ps, ['<think>Visual Reflection</think>', '<think>This Layout is Satisfied</think>', *SOURCES]):
                p.unicode = source
            run()
            self.assertEqual(len(requests), 2)
            self.assertTrue(all('<think>' not in request for request in requests))
            self.assertEqual([get_paragraph_unicode(p) for p in ps[:2]],
                             ['<think>视觉反思</think>', '<think>此布局符合要求</think>'])
            self.assertEqual(recovery.snapshot()[0]['failed'], 0)
            # Accepted output remains bound to the protected input for cache/recovery.
            for p in ps[:2]:
                result = batch.il_translator.accepted_output(p)
                self.assertEqual(recovery.restored(p, result.source), result.output)

    def test_legacy_successes_rebind_without_any_provider_request(self):
        with ExitStack() as stack:
            tmp = stack.enter_context(tempfile.TemporaryDirectory())
            ps, recovery, engine, batch, run = batch_fixtures.PartialBatchTests().fixture(stack, tmp, Mock())
            for p in ps:
                p.unicode = '<b>The experiment produced good results.</b>'
                recovery.record(p, 'succeeded', input=p.unicode, translation='<b>实验取得了良好的结果。</b>')
            run()
            self.assertEqual(engine.requests, [])
            self.assertEqual(recovery.snapshot()[0]['succeeded'], len(ps))
            self.assertTrue(all(get_paragraph_unicode(p) == '<b>实验取得了良好的结果。</b>' for p in ps))
        tags = LiteralTags('<b>One</b><b>Two</b>')
        rebound = tags.protect_existing('<b>一</b><b>二</b>')
        self.assertEqual(tags.restore(rebound), '<b>一</b><b>二</b>')
        with self.assertRaises(InvalidTranslation):
            tags.protect_existing('<b>一</b>')

    def test_code_contents_stay_literal_inside_mixed_prose(self):
        source = 'Use <code>font-family, font-size</code> to change the appearance.'
        tags = LiteralTags(source)
        self.assertEqual(tags.text, 'Use {v1} to change the appearance.')
        output = '使用 {v1} 更改外观。'
        validate_text(tags.text, output, 'zh-CN')
        self.assertEqual(tags.restore(output), '使用 <code>font-family, font-size</code> 更改外观。')

    def test_short_label_cannot_pass_unchanged(self):
        translator = ILTranslator.__new__(ILTranslator)
        translator.translation_config = SimpleNamespace(disable_same_text_fallback=False)
        translator.translate_engine = SimpleNamespace(lang_out='zh-CN')
        tags = LiteralTags('<think>Visual Reflection</think>')
        ti = ILTranslator.TranslateInput(tags.text, [])
        ti.literal_tags = tags
        with self.assertRaises(InvalidTranslation):
            translator.validate_paragraph_output(ti, tags.text)
