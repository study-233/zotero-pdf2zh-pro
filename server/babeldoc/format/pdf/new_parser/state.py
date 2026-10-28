from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from dataclasses import field

Matrix = tuple[float, float, float, float, float, float]
Point = tuple[float, float]
Rect = tuple[float, float, float, float]
PathSegment = (
    tuple[str]
    | tuple[str, float, float]
    | tuple[str, float, float, float, float]
    | tuple[str, float, float, float, float, float, float]
)
OriginalPathPrimitive = tuple[str, tuple[float, ...]]
IDENTITY_MATRIX: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def multiply_matrices(left: Matrix, right: Matrix) -> Matrix:
    a1, b1, c1, d1, e1, f1 = left
    a0, b0, c0, d0, e0, f0 = right
    return (
        a0 * a1 + c0 * b1,
        b0 * a1 + d0 * b1,
        a0 * c1 + c0 * d1,
        b0 * c1 + d0 * d1,
        a0 * e1 + c0 * f1 + e0,
        b0 * e1 + d0 * f1 + f0,
    )


def invert_matrix(matrix: Matrix) -> Matrix:
    a, b, c, d, e, f = matrix
    det = a * d - b * c
    if abs(det) < 1e-10:
        return IDENTITY_MATRIX
    return (
        d / det,
        -b / det,
        -c / det,
        a / det,
        (c * f - d * e) / det,
        (b * e - a * f) / det,
    )


def translate_matrix(tx: float, ty: float) -> Matrix:
    a, b, c, d, e, f = IDENTITY_MATRIX
    return (
        a,
        b,
        c,
        d,
        tx * a + ty * c + e,
        tx * b + ty * d + f,
    )


def translate_existing_matrix(matrix: Matrix, offset: tuple[float, float]) -> Matrix:
    a, b, c, d, e, f = matrix
    tx, ty = offset
    return (
        a,
        b,
        c,
        d,
        tx * a + ty * c + e,
        tx * b + ty * d + f,
    )


def apply_matrix_pt(matrix: Matrix, point: Point) -> Point:
    a, b, c, d, e, f = matrix
    x, y = point
    return a * x + c * y + e, b * x + d * y + f


def apply_matrix_norm(matrix: Matrix, point: Point) -> Point:
    a, b, c, d, _e, _f = matrix
    x, y = point
    return a * x + c * y, b * x + d * y


def get_bound(points: Iterable[Point]) -> tuple[float, float, float, float]:
    pts = list(points)
    if not pts:
        return 0.0, 0.0, 0.0, 0.0

    xs = [point[0] for point in pts]
    ys = [point[1] for point in pts]
    return min(xs), min(ys), max(xs), max(ys)


@dataclass
class GraphicsState:
    ctm: Matrix = IDENTITY_MATRIX
    text_clip_active: bool = False
    text_clip_passthrough_operation: str | None = None
    text_clip_passthrough_ctm: Matrix = IDENTITY_MATRIX


@dataclass
class TextState:
    font_name: str | None = None
    font_size: float = 0.0
    char_spacing: float = 0.0
    word_spacing: float = 0.0
    horizontal_scaling: float = 100.0
    leading: float = 0.0
    rise: float = 0.0
    render_mode: int = 0

    def copy(self) -> TextState:
        return TextState(
            font_name=self.font_name,
            font_size=self.font_size,
            char_spacing=self.char_spacing,
            word_spacing=self.word_spacing,
            horizontal_scaling=self.horizontal_scaling,
            leading=self.leading,
            rise=self.rise,
            render_mode=self.render_mode,
        )


@dataclass
class TextObjectState:
    in_text_object: bool = False
    text_matrix: Matrix = IDENTITY_MATRIX
    line_matrix: Point = (0.0, 0.0)

    def copy(self) -> TextObjectState:
        return TextObjectState(
            in_text_object=self.in_text_object,
            text_matrix=self.text_matrix,
            line_matrix=self.line_matrix,
        )

    def begin(self) -> None:
        self.in_text_object = True
        self.text_matrix = IDENTITY_MATRIX
        self.line_matrix = (0.0, 0.0)

    def end(self) -> None:
        self.in_text_object = False
        self.text_matrix = IDENTITY_MATRIX
        self.line_matrix = (0.0, 0.0)

    def set_text_matrix(self, matrix: Matrix) -> None:
        self.text_matrix = matrix
        self.line_matrix = (0.0, 0.0)

    def move_text_position(self, tx: float, ty: float) -> None:
        a, b, c, d, e, f = self.text_matrix
        self.text_matrix = (
            a,
            b,
            c,
            d,
            tx * a + ty * c + e,
            tx * b + ty * d + f,
        )
        self.line_matrix = (0.0, 0.0)

    def next_line(self, leading: float) -> None:
        a, b, c, d, e, f = self.text_matrix
        self.text_matrix = (
            a,
            b,
            c,
            d,
            leading * c + e,
            leading * d + f,
        )
        self.line_matrix = (0.0, 0.0)

    def set_line_matrix(self, matrix: Point) -> None:
        self.line_matrix = matrix


@dataclass
class InterpreterState:
    graphics_state: GraphicsState = field(default_factory=GraphicsState)
    text_state: TextState = field(default_factory=TextState)
    text_object: TextObjectState = field(default_factory=TextObjectState)
    graphics_stack: list[tuple[GraphicsState, TextState, TextObjectState]] = field(
        default_factory=list
    )

    def push_graphics_state(self) -> None:
        self.graphics_stack.append(
            (
                GraphicsState(
                    ctm=self.graphics_state.ctm,
                    text_clip_active=self.graphics_state.text_clip_active,
                    text_clip_passthrough_operation=(
                        self.graphics_state.text_clip_passthrough_operation
                    ),
                    text_clip_passthrough_ctm=(
                        self.graphics_state.text_clip_passthrough_ctm
                    ),
                ),
                self.text_state.copy(),
                self.text_object.copy(),
            )
        )

    def pop_graphics_state(self) -> None:
        if not self.graphics_stack:
            msg = "Graphics state stack underflow."
            raise ValueError(msg)
        self.graphics_state, self.text_state, self.text_object = (
            self.graphics_stack.pop()
        )
