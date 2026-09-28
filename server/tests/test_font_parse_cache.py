import asyncio
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
import pymupdf
from babeldoc.format.pdf.document_il.frontend.il_creater import ILCreater
from babeldoc.format.pdf.babelpdf.type3 import get_type3_bbox


def type3_document():
    doc=pymupdf.open();page=doc.new_page()
    glyph=doc.get_new_xref();doc.update_object(glyph,'<<>>');doc.update_stream(glyph,b'600 0 0 0 500 700 d1 0 0 500 700 re f')
    font=doc.get_new_xref()
    doc.update_object(font,f'<< /Type /Font /Subtype /Type3 /Name /Test /FontBBox [0 0 500 700] /FontMatrix [.001 0 0 .001 0 0] /FirstChar 65 /LastChar 65 /Widths [600] /CharProcs << /A {glyph} 0 R >> /Encoding << /Type /Encoding /Differences [65 /A] >> >>')
    doc.xref_set_key(page.xref,'Resources',f'<< /Font << /F1 {font} 0 R >> >>')
    return doc,font


def creator(doc):
    obj=ILCreater.__new__(ILCreater)
    obj.mupdf=doc;obj._font_parse_cache={};obj.font_parse_count=0;obj.font_cache_hits=0
    obj.translation_config=SimpleNamespace(raise_if_cancelled=lambda:None)
    return obj

class FontParseCacheTests(unittest.TestCase):
    def test_repeated_type3_is_calculated_once_and_identical(self):
        doc,font=type3_document();self.addCleanup(doc.close)
        obj=creator(doc)
        expected=obj._parse_font_xobj_uncached(font)
        uncached=obj._parse_font_xobj_uncached;obj._parse_font_xobj_uncached=Mock(wraps=uncached)
        for _ in range(15205): result=obj.parse_font_xobj_id(font)
        self.assertEqual(tuple(tuple(b) for b in expected[0]),result[0]);self.assertEqual(expected[1],dict(result[1]))
        self.assertEqual(obj._parse_font_xobj_uncached.call_count,1)
        self.assertEqual(obj.font_cache_hits,15204)
        with self.assertRaises(TypeError): result[1][65]='changed'
        self.assertEqual(doc.page_count,1)

    def test_instances_do_not_share_cache_and_failures_are_not_cached(self):
        a,b=creator(None),creator(None)
        a._parse_font_xobj_uncached=Mock(side_effect=[ValueError('bad'),([[1,2,3,4]],{65:'A'})])
        b._parse_font_xobj_uncached=Mock(return_value=([[9,8,7,6]],{65:'B'}))
        with self.assertRaises(ValueError):a.parse_font_xobj_id(5)
        self.assertEqual(a.parse_font_xobj_id(5)[1][65],'A')
        self.assertEqual(b.parse_font_xobj_id(5)[1][65],'B')
        self.assertEqual(a._parse_font_xobj_uncached.call_count,2)

    def test_cancel_removes_temporary_type3_page(self):
        doc,font=type3_document();self.addCleanup(doc.close)
        def cancel():raise asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):get_type3_bbox(doc,font,cancel)
        self.assertEqual(doc.page_count,1)

    def test_aliases_and_transformed_forms_preserve_intermediate_representation(self):
        import concurrent.futures
        from dataclasses import asdict
        from io import BytesIO
        from unittest.mock import patch
        from babeldoc.format.pdf.high_level import start_parse_il
        from babeldoc.format.pdf.translation_config import TranslationConfig
        from babeldoc.progress_monitor import ProgressMonitor
        doc,font=type3_document()
        form=doc.get_new_xref()
        doc.update_object(form,f'<< /Type /XObject /Subtype /Form /BBox [0 0 50 50] /Resources << /Font << /Alias {font} 0 R >> >> >>')
        doc.update_stream(form,b'BT /Alias 10 Tf 1 0 0 1 0 10 Tm (A) Tj ET 0 0 3 3 re f')
        doc.xref_set_key(doc[0].xref,'Resources',f'<< /Font << /F1 {font} 0 R >> /XObject << /Shape {form} 0 R >> >>')
        content=doc.get_new_xref();doc.update_object(content,'<<>>')
        doc.update_stream(content,b'BT /F1 12 Tf 10 30 Td (A) Tj ET q 1 0 0 1 100 100 cm /Shape Do Q q 2 0 0 2 200 200 cm /Shape Do Q')
        doc[0].set_contents(content)
        pdf=doc.tobytes();doc.close()
        def parse(cached):
            with tempfile.TemporaryDirectory() as temp:
                config=TranslationConfig(translator=SimpleNamespace(),input_file='generated.pdf',lang_in='en',lang_out='zh-CN',doc_layout_model=object(),working_dir=temp,output_dir=temp)
                events=[];config.diagnostic_callback=events.append
                config.progress_monitor=ProgressMonitor([(ILCreater.stage_name,1)],report_interval=60)
                with pymupdf.open(stream=pdf,filetype='pdf') as parsed:
                    il=ILCreater(config);il.mupdf=parsed
                    if not cached:il.parse_font_xobj_id=il._parse_font_xobj_uncached
                    start_parse_il(BytesIO(pdf),doc_zh=parsed,il_creater=il,translation_config=config)
                    return asdict(il.docs),events
        with concurrent.futures.ThreadPoolExecutor(1) as pool:
            expected,_=pool.submit(parse,False).result()
            actual,events=pool.submit(parse,True).result()
        self.assertEqual(actual,expected)
        self.assertEqual([e['operation'] for e in events if e['operation'] in {'page_start','page_end'}],['page_start','page_end'])
        self.assertEqual(events[-1]['completedPages'],1)
