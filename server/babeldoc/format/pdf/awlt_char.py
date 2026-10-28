"""Shared text character carrier used by active parser and legacy baseline."""

from babeldoc.pdfminer.layout import LTChar
from babeldoc.pdfminer.layout import LTComponent
from babeldoc.pdfminer.layout import LTText
from babeldoc.pdfminer.pdfcolor import PDFColorSpace
from babeldoc.pdfminer.pdffont import PDFFont
from babeldoc.pdfminer.pdfinterp import PDFGraphicState
from babeldoc.pdfminer.utils import Matrix
from babeldoc.pdfminer.utils import apply_matrix_pt
from babeldoc.pdfminer.utils import bbox2str
from babeldoc.pdfminer.utils import matrix2str


class AWLTChar(LTChar):
    """Actual letter in the text as a Unicode string."""

    def __init__(
        self,
        matrix: Matrix,
        font: PDFFont,
        fontsize: float,
        scaling: float,
        rise: float,
        text: str,
        textwidth: float,
        textdisp: float | tuple[float | None, float],
        ncs: PDFColorSpace,
        graphicstate: PDFGraphicState,
        xobj_id: int,
        font_id: str,
        render_order: int,
    ) -> None:
        LTText.__init__(self)
        self._text = text
        self.matrix = matrix
        self.fontname = font.fontname
        self.ncs = ncs
        self.graphicstate = graphicstate
        self.xobj_id = xobj_id
        self.adv = textwidth * fontsize * scaling
        self.aw_font_id = font_id
        self.render_order = render_order
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
        a, b, c, d, _e, _f = self.matrix
        self.upright = a * d * scaling > 0 and b * c <= 0
        x0, y0 = apply_matrix_pt(self.matrix, bbox_lower_left)
        x1, y1 = apply_matrix_pt(self.matrix, bbox_upper_right)
        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0
        LTComponent.__init__(self, (x0, y0, x1, y1))
        if font.is_vertical() or matrix[0] == 0:
            self.size = self.width
        else:
            self.size = self.height

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} {bbox2str(self.bbox)} matrix={matrix2str(self.matrix)} font={self.fontname!r} adv={self.adv} text={self.get_text()!r}>"

    def get_text(self) -> str:
        return self._text
