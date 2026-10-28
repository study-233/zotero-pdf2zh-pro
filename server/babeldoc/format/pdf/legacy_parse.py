"""Legacy pdfminer parser pipeline for explicit compatibility tooling."""

from __future__ import annotations

import asyncio
import logging
from asyncio import CancelledError
from typing import Any
from typing import BinaryIO

from pymupdf import Document
from pymupdf import Font

from babeldoc.format.pdf.converter import TranslateConverter
from babeldoc.format.pdf.pdfinterp import PDFPageInterpreterEx
from babeldoc.format.pdf.translation_config import TranslationConfig
from babeldoc.pdfminer.pdfdocument import PDFDocument
from babeldoc.pdfminer.pdfinterp import PDFResourceManager
from babeldoc.pdfminer.pdfpage import PDFPage
from babeldoc.pdfminer.pdfparser import PDFParser

logger = logging.getLogger(__name__)


def start_parse_il(
    inf: BinaryIO,
    pages: list[int] | None = None,
    vfont: str = "",
    vchar: str = "",
    thread: int = 0,
    doc_zh: Document = None,
    lang_in: str = "",
    lang_out: str = "",
    service: str = "",
    resfont: str = "",
    noto: Font = None,
    cancellation_event: asyncio.Event = None,
    il_creater: Any = None,
    translation_config: TranslationConfig = None,
    **kwarg: Any,
) -> None:
    rsrcmgr = PDFResourceManager()
    layout = {}
    device = TranslateConverter(
        rsrcmgr,
        vfont,
        vchar,
        thread,
        layout,
        lang_in,
        lang_out,
        service,
        resfont,
        noto,
        kwarg.get("envs", {}),
        kwarg.get("prompt", []),
        il_creater=il_creater,
    )
    # model = DocLayoutModel.load_available()

    assert device is not None
    assert il_creater is not None
    assert translation_config is not None
    obj_patch = {}
    interpreter = PDFPageInterpreterEx(rsrcmgr, device, obj_patch, il_creater)
    if pages:
        total_pages = len(pages)
    else:
        total_pages = doc_zh.page_count

    il_creater.on_total_pages(total_pages)

    parser = PDFParser(inf)
    doc = PDFDocument(parser)

    for pageno, page in enumerate(PDFPage.create_pages(doc)):
        if cancellation_event and cancellation_event.is_set():
            raise CancelledError("task cancelled")
        if pages and (pageno not in pages):
            continue
        page.pageno = pageno

        if not translation_config.should_translate_page(pageno + 1):
            continue

        height, width = (
            page.cropbox[3] - page.cropbox[1],
            page.cropbox[2] - page.cropbox[0],
        )
        if height > 1200 or width > 2000:
            logger.warning(
                "page %s is too large, maybe unable to translate", pageno + 1
            )
            # continue

        translation_config.raise_if_cancelled()
        # The current program no longer relies on
        # the following layout recognition results,
        # but in order to facilitate the migration of pdf2zh,
        # the relevant code is temporarily retained.
        # pix = doc_zh[page.pageno].get_pixmap()
        # image = np.frombuffer(pix.samples, np.uint8).reshape(
        #     pix.height, pix.width, 3
        # )[:, :, ::-1]
        # page_layout = model.predict(
        #     image, imgsz=int(pix.height / 32) * 32)[0]
        # # kdtree 是不可能 kdtree 的，不如直接渲染成图片，用空间换时间
        # box = np.ones((pix.height, pix.width))
        # h, w = box.shape
        # vcls = ["abandon", "figure", "table",
        #         "isolate_formula", "formula_caption"]
        # for i, d in enumerate(page_layout.boxes):
        #     if page_layout.names[int(d.cls)] not in vcls:
        #         x0, y0, x1, y1 = d.xyxy.squeeze()
        #         x0, y0, x1, y1 = (
        #             np.clip(int(x0 - 1), 0, w - 1),
        #             np.clip(int(h - y1 - 1), 0, h - 1),
        #             np.clip(int(x1 + 1), 0, w - 1),
        #             np.clip(int(h - y0 + 1), 0, h - 1),
        #         )
        #         box[y0:y1, x0:x1] = i + 2
        # for i, d in enumerate(page_layout.boxes):
        #     if page_layout.names[int(d.cls)] in vcls:
        #         x0, y0, x1, y1 = d.xyxy.squeeze()
        #         x0, y0, x1, y1 = (
        #             np.clip(int(x0 - 1), 0, w - 1),
        #             np.clip(int(h - y1 - 1), 0, h - 1),
        #             np.clip(int(x1 + 1), 0, w - 1),
        #             np.clip(int(h - y0 + 1), 0, h - 1),
        #         )
        #         box[y0:y1, x0:x1] = 0
        # layout[page.pageno] = box
        # 新建一个 xref 存放新指令流
        # page.page_xref = doc_zh.get_new_xref()  # hack 插入页面的新 xref
        # doc_zh.update_object(page.page_xref, "<<>>")
        # doc_zh.update_stream(page.page_xref, b"")
        # doc_zh[page.pageno].set_contents(page.page_xref)
        ops_base = interpreter.process_page(page)
        il_creater.on_page_base_operation(ops_base)
        il_creater.on_page_end()
    il_creater.on_finish()
    device.close()
