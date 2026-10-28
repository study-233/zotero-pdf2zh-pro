from __future__ import annotations

from dataclasses import dataclass

from babeldoc.format.pdf.new_parser.interpreter import TextRunEvent
from babeldoc.format.pdf.new_parser.resources import PageResourceBundle
from babeldoc.format.pdf.new_parser.state import Matrix
from babeldoc.format.pdf.new_parser.state import apply_matrix_pt
from babeldoc.format.pdf.new_parser.state import multiply_matrices
from babeldoc.format.pdf.new_parser.state import translate_existing_matrix


@dataclass(frozen=True)
class GlyphEvent:
    cid: int
    unicode_text: str
    font_name: str | None
    font_size: float
    xobject_path: tuple[str, ...]
    text_matrix: Matrix
    glyph_matrix: Matrix
    ctm: Matrix
    glyph_offset: tuple[float, float]
    advance: float
    char_spacing: float
    word_spacing: float
    horizontal_scaling: float
    rise: float
    render_mode: int
    segment_index: int
    glyph_index: int


@dataclass(frozen=True)
class PositionedGlyphEvent:
    cid: int
    unicode_text: str
    font_name: str | None
    font_size: float
    xobject_path: tuple[str, ...]
    text_matrix: Matrix
    glyph_matrix: Matrix
    ctm: Matrix
    glyph_offset: tuple[float, float]
    advance: float
    bbox: tuple[float, float, float, float]
    size: float
    vertical: bool
    char_spacing: float
    word_spacing: float
    horizontal_scaling: float
    rise: float
    render_mode: int
    segment_index: int
    glyph_index: int


def expand_text_run_events(
    events: list[object],
    resource_bundle: PageResourceBundle,
) -> list[GlyphEvent]:
    glyphs: list[GlyphEvent] = []
    for event in events:
        if not isinstance(event, TextRunEvent):
            continue
        font = resource_bundle.get_font(event.xobject_path, event.font_name)
        if font is None:
            continue

        scaling = event.horizontal_scaling * 0.01
        charspace = event.char_spacing * scaling
        wordspace = event.word_spacing * scaling
        if font.is_multibyte():
            wordspace = 0
        dxscale = 0.001 * event.font_size * scaling
        x, y = event.line_matrix
        need_charspace = False
        vertical = font.is_vertical()
        glyph_counter = 0
        base_matrix = multiply_matrices(event.text_matrix, event.ctm)

        for segment_index, segment in enumerate(event.segments):
            if isinstance(segment, float):
                if vertical:
                    y -= segment * dxscale
                else:
                    x -= segment * dxscale
                need_charspace = True
                continue

            for cid in font.decode(segment):
                if need_charspace:
                    if vertical:
                        y += charspace
                    else:
                        x += charspace

                unicode_text = font.unicode_text(cid, f"(cid:{cid})")

                advance = font.char_width(cid) * event.font_size * scaling
                glyph_offset = (x, y)
                glyph_matrix = translate_existing_matrix(base_matrix, glyph_offset)
                glyphs.append(
                    GlyphEvent(
                        cid=cid,
                        unicode_text=unicode_text,
                        font_name=event.font_name,
                        font_size=event.font_size,
                        xobject_path=event.xobject_path,
                        text_matrix=event.text_matrix,
                        glyph_matrix=glyph_matrix,
                        ctm=event.ctm,
                        glyph_offset=glyph_offset,
                        advance=advance,
                        char_spacing=event.char_spacing,
                        word_spacing=event.word_spacing,
                        horizontal_scaling=event.horizontal_scaling,
                        rise=event.rise,
                        render_mode=event.render_mode,
                        segment_index=segment_index,
                        glyph_index=glyph_counter,
                    )
                )
                glyph_counter += 1

                if vertical:
                    y += advance
                    if cid == 32 and wordspace:
                        y += wordspace
                else:
                    x += advance
                    if cid == 32 and wordspace:
                        x += wordspace

                need_charspace = True
    return glyphs


def position_glyph_events(
    glyph_events: list[GlyphEvent],
    resource_bundle: PageResourceBundle,
) -> list[PositionedGlyphEvent]:
    positioned: list[PositionedGlyphEvent] = []
    for glyph in glyph_events:
        font = resource_bundle.get_font(glyph.xobject_path, glyph.font_name)
        if font is None:
            continue

        if font.is_vertical():
            textdisp = font.char_disp(glyph.cid)
            assert isinstance(textdisp, tuple)
            vx, vy = textdisp
            if vx is None:
                vx = glyph.font_size * 0.5
            else:
                vx = vx * glyph.font_size * 0.001
            vy = (1000 - vy) * glyph.font_size * 0.001
            bbox_lower_left = (-vx, vy + glyph.rise + glyph.advance)
            bbox_upper_right = (-vx + glyph.font_size, vy + glyph.rise)
            vertical = True
        else:
            descent = font.get_descent() * glyph.font_size
            bbox_lower_left = (0, descent + glyph.rise)
            bbox_upper_right = (
                glyph.advance,
                descent + glyph.rise + glyph.font_size,
            )
            vertical = glyph.glyph_matrix[0] == 0 and glyph.glyph_matrix[3] == 0

        x0, y0 = apply_matrix_pt(glyph.glyph_matrix, bbox_lower_left)
        x1, y1 = apply_matrix_pt(glyph.glyph_matrix, bbox_upper_right)
        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0

        width = x1 - x0
        height = y1 - y0
        size = width if vertical or glyph.glyph_matrix[0] == 0 else height
        positioned.append(
            PositionedGlyphEvent(
                cid=glyph.cid,
                unicode_text=glyph.unicode_text,
                font_name=glyph.font_name,
                font_size=glyph.font_size,
                xobject_path=glyph.xobject_path,
                text_matrix=glyph.text_matrix,
                glyph_matrix=glyph.glyph_matrix,
                ctm=glyph.ctm,
                glyph_offset=glyph.glyph_offset,
                advance=glyph.advance,
                bbox=(x0, y0, x1, y1),
                size=size,
                vertical=vertical,
                char_spacing=glyph.char_spacing,
                word_spacing=glyph.word_spacing,
                horizontal_scaling=glyph.horizontal_scaling,
                rise=glyph.rise,
                render_mode=glyph.render_mode,
                segment_index=glyph.segment_index,
                glyph_index=glyph.glyph_index,
            )
        )
    return positioned
