from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import Protocol

from babeldoc.format.pdf.new_parser.font_types import PdfFontLike
from babeldoc.format.pdf.new_parser.state import Matrix
from babeldoc.format.pdf.new_parser.state import PathSegment
from babeldoc.format.pdf.new_parser.state import Point
from babeldoc.format.pdf.new_parser.state import Rect
from babeldoc.format.pdf.new_parser.state import apply_matrix_pt
from babeldoc.format.pdf.new_parser.state import get_bound


class PDFColorSpaceLike(Protocol):
    name: str
    ncomponents: int


@dataclass
class GraphicStateSnapshot:
    passthrough_instruction: list[tuple[str, str]] = field(default_factory=list)


class EmitterSink(Protocol):
    @property
    def current_page_font_name_id_map(self): ...

    @property
    def current_clip_paths(self): ...

    @property
    def passthrough_per_char_instruction(self): ...

    @property
    def xobj_id(self) -> int: ...

    def get_render_order_and_increase(self) -> int: ...
    def on_lt_char(self, item) -> None: ...
    def on_lt_curve(self, item) -> None: ...
    def on_pdf_figure(self, item) -> None: ...
    def on_page_media_box(self, x: float, y: float, x2: float, y2: float) -> None: ...
    def on_page_number(self, page_number: int) -> None: ...


class LightChar:
    def __init__(
        self,
        matrix: Matrix,
        font: PdfFontLike,
        fontsize: float,
        scaling: float,
        rise: float,
        text: str,
        textwidth: float,
        textdisp: float | tuple[float | None, float],
        ncs: PDFColorSpaceLike,
        graphicstate: GraphicStateSnapshot,
        xobj_id: int,
        font_id: str | None,
        render_order: int,
        cid: int,
    ) -> None:
        self._text = text
        self.matrix = matrix
        self.fontname = font.fontname
        self.ncs = ncs
        self.graphicstate = graphicstate
        self.xobj_id = xobj_id
        self.adv = textwidth * fontsize * scaling
        self.aw_font_id = font_id
        self.render_order = render_order
        self.cid = cid
        self.font = font

        if font.is_vertical():
            assert isinstance(textdisp, tuple)
            vx, vy = textdisp
            if vx is None:
                vx = fontsize * 0.5
            else:
                vx = vx * fontsize * 0.001
            vy = (1000 - vy) * fontsize * 0.001
            bbox_lower_left = (-vx, vy + rise + self.adv)
            bbox_upper_right = (-vx + fontsize, vy + rise)
        else:
            descent = font.get_descent() * fontsize
            bbox_lower_left = (0, descent + rise)
            bbox_upper_right = (self.adv, descent + rise + fontsize)

        x0, y0 = apply_matrix_pt(self.matrix, bbox_lower_left)
        x1, y1 = apply_matrix_pt(self.matrix, bbox_upper_right)
        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0

        self.bbox = (x0, y0, x1, y1)
        self.width = x1 - x0
        self.height = y1 - y0
        if font.is_vertical() or matrix[0] == 0:
            self.size = self.width
        else:
            self.size = self.height

    def get_text(self) -> str:
        return self._text


class LightCurve:
    def __init__(
        self,
        pts: list[Point],
        stroke: bool,
        fill: bool,
        evenodd: bool,
        transformed_path: list[PathSegment],
        passthrough_instruction: list[tuple[str, str]],
        xobj_id: int,
        render_order: int,
        ctm: Matrix,
        raw_path: list[PathSegment],
        clip_paths: list[tuple],
    ) -> None:
        if pts:
            xs = [pt[0] for pt in pts]
            ys = [pt[1] for pt in pts]
            self.bbox = (min(xs), min(ys), max(xs), max(ys))
        else:
            self.bbox = (0.0, 0.0, 0.0, 0.0)
        self.stroke = stroke
        self.fill = fill
        self.evenodd = evenodd
        self.original_path = transformed_path
        self.passthrough_instruction = passthrough_instruction
        self.xobj_id = xobj_id
        self.render_order = render_order
        self.ctm = ctm
        self.raw_path = raw_path
        self.clip_paths = clip_paths


class FigureMarker:
    def __init__(self, bbox: Rect, matrix: Matrix) -> None:
        x, y, w, h = bbox
        bounds = ((x, y), (x + w, y), (x, y + h), (x + w, y + h))
        self.bbox = get_bound(apply_matrix_pt(matrix, (p, q)) for (p, q) in bounds)


class RenderContainer:
    def __init__(self, marker: FigureMarker | None = None) -> None:
        self.marker = marker
        self.items: list[object] = []
