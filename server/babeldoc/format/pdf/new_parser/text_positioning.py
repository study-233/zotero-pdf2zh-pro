from __future__ import annotations

from typing import Protocol

from babeldoc.format.pdf.awlt_char import AWLTChar
from babeldoc.format.pdf.new_parser.bridge_types import GraphicStateSnapshot
from babeldoc.format.pdf.new_parser.interpreter import TextRunEvent
from babeldoc.format.pdf.new_parser.resources import PageResourceBundle
from babeldoc.format.pdf.new_parser.state import multiply_matrices
from babeldoc.format.pdf.new_parser.state import translate_existing_matrix
from babeldoc.format.pdf.new_parser.tokenizer import canonical_pdf_name


class TextRunPositioner(Protocol):
    def position_text_run(
        self,
        event: TextRunEvent,
        resource_bundle: PageResourceBundle,
        *,
        xobj_id: int,
    ) -> list[AWLTChar]: ...


class NativeTextRunPositioner:
    def position_text_run(
        self,
        event: TextRunEvent,
        resource_bundle: PageResourceBundle,
        *,
        xobj_id: int,
    ) -> list[AWLTChar]:
        if event.font_name is None:
            return []
        font = resource_bundle.get_font(event.xobject_path, event.font_name)
        if font is None:
            return []

        matrix = multiply_matrices(event.text_matrix, event.ctm)
        fontsize = event.font_size
        scaling = event.horizontal_scaling * 0.01
        charspace = event.char_spacing * scaling
        wordspace = event.word_spacing * scaling
        if font.is_multibyte():
            wordspace = 0
        dxscale = 0.001 * fontsize * scaling
        pos_x, pos_y = event.line_matrix

        chars: list[AWLTChar] = []
        graphicstate = GraphicStateSnapshot()

        need_charspace = False
        for obj in event.segments:
            if isinstance(obj, int | float):
                if font.is_vertical():
                    pos_y -= obj * dxscale
                else:
                    pos_x -= obj * dxscale
                need_charspace = True
                continue
            if not isinstance(obj, bytes):
                continue
            for cid in font.decode(obj):
                if need_charspace:
                    if font.is_vertical():
                        pos_y += charspace
                    else:
                        pos_x += charspace
                char_matrix = translate_existing_matrix(matrix, (pos_x, pos_y))
                text = font.unicode_text(cid, f"(cid:{cid})")
                font_id = getattr(font, "font_id_temp", None) or canonical_pdf_name(
                    event.font_name
                )
                item = AWLTChar(
                    char_matrix,
                    font,
                    fontsize,
                    scaling,
                    event.rise,
                    text,
                    font.char_width(cid),
                    font.char_disp(cid),
                    None,
                    graphicstate,
                    xobj_id,
                    font_id,
                    0,
                )
                item.cid = cid
                item.font = font
                item.render_mode = event.render_mode
                chars.append(item)
                if font.is_vertical():
                    pos_y += item.adv
                    if cid == 32 and wordspace:
                        pos_y += wordspace
                else:
                    pos_x += item.adv
                    if cid == 32 and wordspace:
                        pos_x += wordspace
                need_charspace = True
        return chars


DEFAULT_NATIVE_TEXT_RUN_POSITIONER = NativeTextRunPositioner()
