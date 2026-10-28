from __future__ import annotations

from dataclasses import dataclass

from babeldoc.format.pdf.new_parser.pdf_token_serializer import serialize_pdf_token
from babeldoc.format.pdf.new_parser.state import InterpreterState
from babeldoc.format.pdf.new_parser.state import Matrix
from babeldoc.format.pdf.new_parser.state import OriginalPathPrimitive
from babeldoc.format.pdf.new_parser.state import PathSegment
from babeldoc.format.pdf.new_parser.state import Point
from babeldoc.format.pdf.new_parser.state import multiply_matrices
from babeldoc.format.pdf.new_parser.tokenizer import PdfName
from babeldoc.format.pdf.new_parser.tokenizer import PdfOperation
from babeldoc.format.pdf.new_parser.tokenizer import PdfString
from babeldoc.format.pdf.new_parser.tokenizer import decode_pdf_name


class UnsupportedOperatorError(ValueError):
    pass


@dataclass(frozen=True)
class TextRunEvent:
    operator: str
    segments: tuple[bytes | float, ...]
    text_matrix: Matrix
    line_matrix: Point
    ctm: Matrix
    font_name: str | None
    font_size: float
    char_spacing: float
    word_spacing: float
    horizontal_scaling: float
    leading: float
    rise: float
    render_mode: int
    xobject_path: tuple[str, ...]


@dataclass(frozen=True)
class SetFontEvent:
    font_name: str
    font_size: float


@dataclass(frozen=True)
class ConcatMatrixEvent:
    matrix: Matrix


@dataclass(frozen=True)
class GraphicStateOpEvent:
    operator: str
    args: tuple[object, ...]
    ctm: Matrix


@dataclass(frozen=True)
class ShadingPaintEvent:
    name: PdfName
    ctm: Matrix
    xobject_path: tuple[str, ...]


@dataclass(frozen=True)
class SaveGraphicsStateEvent:
    pass


@dataclass(frozen=True)
class RestoreGraphicsStateEvent:
    pass


@dataclass(frozen=True)
class TextMatrixEvent:
    matrix: Matrix


@dataclass(frozen=True)
class BeginTextObjectEvent:
    pass


@dataclass(frozen=True)
class EndTextObjectEvent:
    pass


@dataclass(frozen=True)
class BeginXObjectEvent:
    name: str
    subtype: str
    bbox: tuple[float, float, float, float]
    matrix: Matrix
    ctm: Matrix
    xref_id: int | None
    xobject_path: tuple[str, ...]


@dataclass(frozen=True)
class EndXObjectEvent:
    name: str
    subtype: str


@dataclass(frozen=True)
class ImageXObjectEvent:
    name: str
    bbox: tuple[float, float, float, float]
    matrix: Matrix
    ctm: Matrix
    xref_id: int | None
    xobject_path: tuple[str, ...]
    text_clip_active: bool = False
    text_clip_passthrough_operation: str | None = None
    text_clip_passthrough_ctm: Matrix | None = None


@dataclass(frozen=True)
class PathPaintEvent:
    path: tuple[PathSegment, ...]
    stroke: bool
    fill: bool
    evenodd: bool
    ctm: Matrix
    xobject_path: tuple[str, ...]
    original_path_primitive: OriginalPathPrimitive | None = None


@dataclass(frozen=True)
class ClipPathEvent:
    path: tuple[PathSegment, ...]
    evenodd: bool
    ctm: Matrix
    xobject_path: tuple[str, ...]


@dataclass(frozen=True)
class InlineImageEvent:
    stream: object
    ctm: Matrix
    xobject_path: tuple[str, ...]


class CollectingEventSink:
    def __init__(self) -> None:
        self.events: list[object] = []

    def emit(self, event: object) -> None:
        self.events.append(event)


class TextContentInterpreter:
    SUPPORTED_OPERATORS = {
        "q",
        "Q",
        "cm",
        "g",
        "G",
        "rg",
        "RG",
        "k",
        "K",
        "cs",
        "CS",
        "ri",
        "gs",
        "sc",
        "SC",
        "scn",
        "SCN",
        "w",
        "i",
        "J",
        "j",
        "M",
        "d",
        "m",
        "l",
        "c",
        "v",
        "y",
        "h",
        "re",
        "S",
        "s",
        "f",
        "F",
        "f*",
        "B",
        "B*",
        "b",
        "b*",
        "n",
        "W",
        "W*",
        "sh",
        "MP",
        "DP",
        "BMC",
        "BDC",
        "EMC",
        "BX",
        "EX",
        "INLINE_IMAGE",
        "Do",
        "BT",
        "ET",
        "Tf",
        "Tc",
        "Tw",
        "Tz",
        "TL",
        "Ts",
        "Tr",
        "Tm",
        "Td",
        "TD",
        "T*",
        "Tj",
        "TJ",
        "'",
        '"',
    }

    def __init__(self, sink: CollectingEventSink | None = None) -> None:
        self.state = InterpreterState()
        self.sink = sink or CollectingEventSink()
        self.xobject_handler = None
        self.xobject_path: tuple[str, ...] = ()
        self.font_resolver = None
        self.argstack: list[object] = []
        self.current_path: list[PathSegment] = []
        self.current_path_original_primitive: OriginalPathPrimitive | None = None
        self.pending_clip_evenodd: bool | None = None
        self.current_text_object_operations: list[str] = []
        self.current_text_object_has_clip_passthrough = False
        self.current_text_clip_passthrough_ctm: Matrix | None = None
        self.operator_to_handler = {
            "q": self._op_q,
            "Q": self._op_q_restore,
            "cm": self._op_cm,
            "g": lambda operands: self._op_set_nonstroking_color(operands, "g"),
            "G": lambda operands: self._op_set_stroking_color(operands, "G"),
            "rg": lambda operands: self._op_set_nonstroking_color(operands, "rg"),
            "RG": lambda operands: self._op_set_stroking_color(operands, "RG"),
            "k": lambda operands: self._op_set_nonstroking_color(operands, "k"),
            "K": lambda operands: self._op_set_stroking_color(operands, "K"),
            "cs": self._op_set_nonstroking_colorspace,
            "CS": self._op_set_stroking_colorspace,
            "ri": self._op_set_intent,
            "gs": self._op_set_extgstate,
            "sc": lambda operands: self._op_set_nonstroking_color(operands, "sc"),
            "SC": lambda operands: self._op_set_stroking_color(operands, "SC"),
            "scn": lambda operands: self._op_set_nonstroking_color(operands, "scn"),
            "SCN": lambda operands: self._op_set_stroking_color(operands, "SCN"),
            "w": self._op_set_linewidth,
            "i": self._op_set_flatness,
            "J": self._op_set_linecap,
            "j": self._op_set_linejoin,
            "M": self._op_set_miterlimit,
            "d": self._op_set_line_dash,
            "m": self._op_m,
            "l": self._op_l,
            "c": self._op_c,
            "v": self._op_v,
            "y": self._op_y,
            "h": self._op_h,
            "re": self._op_re,
            "S": self._op_stroke,
            "s": self._op_close_and_stroke,
            "f": self._op_fill,
            "F": self._op_fill,
            "f*": self._op_fill_evenodd,
            "B": self._op_fill_and_stroke,
            "B*": self._op_fill_and_stroke_evenodd,
            "b": self._op_close_fill_and_stroke,
            "b*": self._op_close_fill_and_stroke_evenodd,
            "n": self._op_end_path,
            "W": self._op_w_clip,
            "W*": self._op_w_clip_evenodd,
            "sh": self._op_set_shading,
            "MP": self._op_ignore,
            "DP": self._op_ignore,
            "BMC": self._op_ignore,
            "BDC": self._op_ignore,
            "EMC": self._op_ignore,
            "BX": self._op_ignore,
            "EX": self._op_ignore,
            "INLINE_IMAGE": self._op_inline_image,
            "Do": self._op_do,
            "BT": self._op_bt,
            "ET": self._op_et,
            "Tf": self._op_tf,
            "Tc": self._op_tc,
            "Tw": self._op_tw,
            "Tz": self._op_tz,
            "TL": self._op_tl,
            "Ts": self._op_ts,
            "Tr": self._op_tr,
            "Tm": self._op_tm,
            "Td": self._op_td,
            "TD": self._op_td_set_leading,
            "T*": self._op_t_star,
            "Tj": self._op_tj,
            "TJ": self._op_tj_array,
            "'": self._op__tick,
            '"': self._op__quote,
        }

    def run(self, operations: list[PdfOperation]) -> list[object]:
        for operation in operations:
            self.execute(operation)
        return self.sink.events

    def execute(self, operation: PdfOperation) -> None:
        operator = operation.operator
        if operator not in self.SUPPORTED_OPERATORS:
            raise UnsupportedOperatorError(operator)
        self._record_text_object_operation(operation)
        handler = self.operator_to_handler[operator]
        operands = [*self.argstack, *operation.operands]
        self.argstack.clear()
        handler(operands)

    def _op_q(self, operands: list[object]) -> None:
        self._require_arity("q", operands, 0)
        self.state.push_graphics_state()
        self.sink.emit(SaveGraphicsStateEvent())

    def _op_q_restore(self, operands: list[object]) -> None:
        self._require_arity("Q", operands, 0)
        if self.state.graphics_stack:
            self.state.pop_graphics_state()
        self.sink.emit(RestoreGraphicsStateEvent())

    def _op_cm(self, operands: list[object]) -> None:
        self._require_arity("cm", operands, 6)
        matrix = self._matrix_from_operands(operands)
        self.state.graphics_state.ctm = multiply_matrices(
            matrix,
            self.state.graphics_state.ctm,
        )
        self.sink.emit(ConcatMatrixEvent(self.state.graphics_state.ctm))

    def _op_ignore(self, operands: list[object]) -> None:
        _ = operands

    def _emit_graphic_state_op(self, operator: str, args: list[object]) -> None:
        self.sink.emit(
            GraphicStateOpEvent(
                operator=operator,
                args=tuple(args),
                ctm=self.state.graphics_state.ctm,
            )
        )

    def _op_set_linewidth(self, operands: list[object]) -> None:
        self._require_arity("w", operands, 1)
        self._emit_graphic_state_op("w", operands)

    def _op_set_flatness(self, operands: list[object]) -> None:
        self._require_arity("i", operands, 1)
        self._emit_graphic_state_op("i", operands)

    def _op_set_linecap(self, operands: list[object]) -> None:
        self._require_arity("J", operands, 1)
        self._emit_graphic_state_op("J", operands)

    def _op_set_linejoin(self, operands: list[object]) -> None:
        self._require_arity("j", operands, 1)
        self._emit_graphic_state_op("j", operands)

    def _op_set_miterlimit(self, operands: list[object]) -> None:
        self._require_arity("M", operands, 1)
        self._emit_graphic_state_op("M", operands)

    def _op_set_intent(self, operands: list[object]) -> None:
        self._require_arity("ri", operands, 1)
        self._emit_graphic_state_op("ri", operands)

    def _op_set_extgstate(self, operands: list[object]) -> None:
        self._require_arity("gs", operands, 1)
        self._emit_graphic_state_op("gs", operands)

    def _op_set_shading(self, operands: list[object]) -> None:
        self._require_arity("sh", operands, 1)
        [name] = operands
        if not isinstance(name, PdfName):
            raise ValueError("sh requires a PdfName operand")
        self.sink.emit(
            ShadingPaintEvent(
                name=name,
                ctm=self.state.graphics_state.ctm,
                xobject_path=self.xobject_path,
            )
        )

    def _op_set_stroking_colorspace(self, operands: list[object]) -> None:
        self._require_arity("CS", operands, 1)
        self._emit_graphic_state_op("CS", operands)

    def _op_set_nonstroking_colorspace(self, operands: list[object]) -> None:
        self._require_arity("cs", operands, 1)
        self._emit_graphic_state_op("cs", operands)

    def _op_set_stroking_color(self, operands: list[object], operator: str) -> None:
        self._emit_graphic_state_op(operator, operands)

    def _op_set_nonstroking_color(self, operands: list[object], operator: str) -> None:
        self._emit_graphic_state_op(operator, operands)

    def _op_set_line_dash(self, operands: list[object]) -> None:
        self._require_arity("d", operands, 2)
        self._emit_graphic_state_op("d", operands)

    def _op_m(self, operands: list[object]) -> None:
        values = self._recover_path_numbers("m", operands, 2)
        if values is None:
            return
        if self.current_path:
            self.current_path_original_primitive = None
        self.current_path.append(("m", values[0], values[1]))

    def _op_l(self, operands: list[object]) -> None:
        values = self._recover_path_numbers("l", operands, 2)
        if values is None:
            return
        self.current_path_original_primitive = None
        self.current_path.append(("l", values[0], values[1]))

    def _op_c(self, operands: list[object]) -> None:
        values = self._recover_path_numbers("c", operands, 6)
        if values is None:
            return
        self.current_path_original_primitive = None
        self.current_path.append(("c", *values))

    def _op_v(self, operands: list[object]) -> None:
        values = self._recover_path_numbers("v", operands, 4)
        if values is None:
            return
        self.current_path_original_primitive = None
        self.current_path.append(("v", *values))

    def _op_y(self, operands: list[object]) -> None:
        values = self._recover_path_numbers("y", operands, 4)
        if values is None:
            return
        self.current_path_original_primitive = None
        self.current_path.append(("y", *values))

    def _op_h(self, operands: list[object]) -> None:
        self._require_arity("h", operands, 0)
        self.current_path_original_primitive = None
        self.current_path.append(("h",))

    def _op_re(self, operands: list[object]) -> None:
        values = self._recover_path_numbers("re", operands, 4)
        if values is None:
            return
        x, y, w, h = values
        if self.current_path:
            self.current_path_original_primitive = None
        else:
            self.current_path_original_primitive = ("re", (x, y, w, h))
        self.current_path.extend(
            [
                ("m", x, y),
                ("l", x + w, y),
                ("l", x + w, y + h),
                ("l", x, y + h),
                ("h",),
            ]
        )

    def _emit_clip_path(self) -> None:
        if self.current_path and self.pending_clip_evenodd is not None:
            self.sink.emit(
                ClipPathEvent(
                    path=tuple(self.current_path),
                    evenodd=self.pending_clip_evenodd,
                    ctm=self.state.graphics_state.ctm,
                    xobject_path=self.xobject_path,
                )
            )
            self.pending_clip_evenodd = None

    def _emit_path_paint(self, *, stroke: bool, fill: bool, evenodd: bool) -> None:
        self._emit_clip_path()
        if self.current_path:
            self.sink.emit(
                PathPaintEvent(
                    path=tuple(self.current_path),
                    stroke=stroke,
                    fill=fill,
                    evenodd=evenodd,
                    ctm=self.state.graphics_state.ctm,
                    xobject_path=self.xobject_path,
                    original_path_primitive=self.current_path_original_primitive,
                )
            )
        self.current_path = []
        self.current_path_original_primitive = None

    def _op_stroke(self, operands: list[object]) -> None:
        self._require_arity("S", operands, 0)
        self._emit_path_paint(stroke=True, fill=False, evenodd=False)

    def _op_close_and_stroke(self, operands: list[object]) -> None:
        self._require_arity("s", operands, 0)
        self.current_path.append(("h",))
        self._emit_path_paint(stroke=True, fill=False, evenodd=False)

    def _op_fill(self, operands: list[object]) -> None:
        self._require_arity("f", operands, 0)
        self._emit_path_paint(stroke=False, fill=True, evenodd=False)

    def _op_fill_evenodd(self, operands: list[object]) -> None:
        self._require_arity("f*", operands, 0)
        self._emit_path_paint(stroke=False, fill=True, evenodd=True)

    def _op_fill_and_stroke(self, operands: list[object]) -> None:
        self._require_arity("B", operands, 0)
        self._emit_path_paint(stroke=True, fill=True, evenodd=False)

    def _op_fill_and_stroke_evenodd(self, operands: list[object]) -> None:
        self._require_arity("B*", operands, 0)
        self._emit_path_paint(stroke=True, fill=True, evenodd=True)

    def _op_close_fill_and_stroke(self, operands: list[object]) -> None:
        self._require_arity("b", operands, 0)
        self.current_path.append(("h",))
        self._emit_path_paint(stroke=True, fill=True, evenodd=False)

    def _op_close_fill_and_stroke_evenodd(self, operands: list[object]) -> None:
        self._require_arity("b*", operands, 0)
        self.current_path.append(("h",))
        self._emit_path_paint(stroke=True, fill=True, evenodd=True)

    def _op_end_path(self, operands: list[object]) -> None:
        self._require_arity("n", operands, 0)
        self._emit_clip_path()
        self.current_path = []
        self.current_path_original_primitive = None

    def _op_clip(self, operands: list[object], *, evenodd: bool) -> None:
        self._require_arity("W*" if evenodd else "W", operands, 0)
        self.pending_clip_evenodd = evenodd

    def _op_w_clip(self, operands: list[object]) -> None:
        self._op_clip(operands, evenodd=False)

    def _op_w_clip_evenodd(self, operands: list[object]) -> None:
        self._op_clip(operands, evenodd=True)

    def _op_inline_image(self, operands: list[object]) -> None:
        self._require_arity("INLINE_IMAGE", operands, 1)
        stream = operands[0]
        if not hasattr(stream, "get_data"):
            raise ValueError(f"Expected stream-like operand, got {type(stream)}.")
        self.sink.emit(
            InlineImageEvent(
                stream=stream,
                ctm=self.state.graphics_state.ctm,
                xobject_path=self.xobject_path,
            )
        )

    def _op_do(self, operands: list[object]) -> None:
        self._require_arity("Do", operands, 1)
        if self.xobject_handler is None:
            raise UnsupportedOperatorError("Do")
        name = self._require_name(operands[0])
        for event in self.xobject_handler(name, self.state):
            self.sink.emit(event)

    def _op_bt(self, operands: list[object]) -> None:
        self._require_arity("BT", operands, 0)
        self.current_text_object_operations = [self._serialize_operation("BT", [])]
        self.current_text_object_has_clip_passthrough = False
        self.current_text_clip_passthrough_ctm = None
        self.state.text_object.begin()
        self.sink.emit(BeginTextObjectEvent())

    def _op_et(self, operands: list[object]) -> None:
        self._require_arity("ET", operands, 0)
        self.state.text_object.end()
        if (
            self.current_text_object_has_clip_passthrough
            and self.current_text_clip_passthrough_ctm is not None
        ):
            self.state.graphics_state.text_clip_passthrough_operation = " ".join(
                self.current_text_object_operations
            )
            self.state.graphics_state.text_clip_passthrough_ctm = (
                self.current_text_clip_passthrough_ctm
            )
        self.sink.emit(EndTextObjectEvent())

    def _op_tf(self, operands: list[object]) -> None:
        self._require_arity("Tf", operands, 2)
        name = decode_pdf_name(self._require_name(operands[0]))
        size = self._require_number(operands[1])
        self.state.text_state.font_name = name
        self.state.text_state.font_size = size
        self.sink.emit(SetFontEvent(name, size))

    def _op_tc(self, operands: list[object]) -> None:
        self._require_arity("Tc", operands, 1)
        self.state.text_state.char_spacing = self._require_number(operands[0])

    def _op_tw(self, operands: list[object]) -> None:
        self._require_arity("Tw", operands, 1)
        self.state.text_state.word_spacing = self._require_number(operands[0])

    def _op_tz(self, operands: list[object]) -> None:
        self._require_arity("Tz", operands, 1)
        self.state.text_state.horizontal_scaling = self._require_number(operands[0])

    def _op_tl(self, operands: list[object]) -> None:
        self._require_arity("TL", operands, 1)
        self.state.text_state.leading = -self._require_number(operands[0])

    def _op_ts(self, operands: list[object]) -> None:
        self._require_arity("Ts", operands, 1)
        self.state.text_state.rise = self._require_number(operands[0])

    def _op_tr(self, operands: list[object]) -> None:
        self._require_arity("Tr", operands, 1)
        self.state.text_state.render_mode = int(self._require_number(operands[0]))

    def _op_tm(self, operands: list[object]) -> None:
        self._require_arity("Tm", operands, 6)
        matrix = self._matrix_from_operands(operands)
        self.state.text_object.set_text_matrix(matrix)
        self.sink.emit(TextMatrixEvent(matrix))

    def _op_td(self, operands: list[object]) -> None:
        self._require_arity("Td", operands, 2)
        tx = self._require_number(operands[0])
        ty = self._require_number(operands[1])
        self.state.text_object.move_text_position(tx, ty)

    def _op_td_set_leading(self, operands: list[object]) -> None:
        self._require_arity("TD", operands, 2)
        tx = self._require_number(operands[0])
        ty = self._require_number(operands[1])
        self.state.text_state.leading = ty
        self.state.text_object.move_text_position(tx, ty)

    def _op_t_star(self, operands: list[object]) -> None:
        self._require_arity("T*", operands, 0)
        self.state.text_object.next_line(self.state.text_state.leading)

    def _op_tj(self, operands: list[object]) -> None:
        self._require_arity("Tj", operands, 1)
        self._emit_text_show("Tj", self._require_string(operands[0]).raw)

    def _op_tj_array(self, operands: list[object]) -> None:
        self._require_arity("TJ", operands, 1)
        seq = operands[0]
        if not isinstance(seq, list):
            raise ValueError("TJ expects a single array operand.")
        values: list[bytes | float] = []
        for item in seq:
            if isinstance(item, PdfString):
                values.append(item.raw)
            else:
                values.append(self._require_number(item))
        self._emit_text_show("TJ", values)

    def _op__tick(self, operands: list[object]) -> None:
        self._require_arity("'", operands, 1)
        self.state.text_object.next_line(self.state.text_state.leading)
        self._emit_text_show("'", self._require_string(operands[0]).raw)

    def _op__quote(self, operands: list[object]) -> None:
        self._require_arity('"', operands, 3)
        self.state.text_state.word_spacing = self._require_number(operands[0])
        self.state.text_state.char_spacing = self._require_number(operands[1])
        self.state.text_object.next_line(self.state.text_state.leading)
        self._emit_text_show('"', self._require_string(operands[2]).raw)

    def _emit_text_show(self, operator: str, text: bytes | list[bytes | float]) -> None:
        if not self.state.text_object.in_text_object:
            raise ValueError(f"{operator} used outside a text object.")
        is_clip_only_text = self.state.text_state.render_mode == 7
        if self.state.text_state.render_mode in {4, 5, 6, 7}:
            self.state.graphics_state.text_clip_active = True
            if is_clip_only_text:
                self.current_text_object_has_clip_passthrough = True
                self.current_text_clip_passthrough_ctm = self.state.graphics_state.ctm
        segments: tuple[bytes | float, ...]
        if isinstance(text, bytes):
            segments = (text,)
        else:
            segments = tuple(text)
        if is_clip_only_text:
            self._advance_line_matrix(segments)
            return
        self.sink.emit(
            TextRunEvent(
                operator=operator,
                segments=segments,
                text_matrix=self.state.text_object.text_matrix,
                line_matrix=self.state.text_object.line_matrix,
                ctm=self.state.graphics_state.ctm,
                font_name=self.state.text_state.font_name,
                font_size=self.state.text_state.font_size,
                char_spacing=self.state.text_state.char_spacing,
                word_spacing=self.state.text_state.word_spacing,
                horizontal_scaling=self.state.text_state.horizontal_scaling,
                leading=self.state.text_state.leading,
                rise=self.state.text_state.rise,
                render_mode=self.state.text_state.render_mode,
                xobject_path=self.xobject_path,
            )
        )
        self._advance_line_matrix(segments)

    def _record_text_object_operation(self, operation: PdfOperation) -> None:
        if operation.operator == "BT":
            return
        if not self.state.text_object.in_text_object:
            return
        self.current_text_object_operations.append(
            self._serialize_operation(operation.operator, operation.operands)
        )

    @staticmethod
    def _serialize_operation(operator: str, operands: list[object]) -> str:
        if not operands:
            return operator
        return (
            f"{' '.join(serialize_pdf_token(operand) for operand in operands)} "
            f"{operator}"
        )

    def _advance_line_matrix(self, segments: tuple[bytes | float, ...]) -> None:
        font_name = self.state.text_state.font_name
        if font_name is None or self.font_resolver is None:
            return

        font = self.font_resolver(self.xobject_path, font_name)
        if font is None:
            return

        scaling = self.state.text_state.horizontal_scaling * 0.01
        charspace = self.state.text_state.char_spacing * scaling
        wordspace = self.state.text_state.word_spacing * scaling
        fontsize = self.state.text_state.font_size
        dxscale = 0.001 * fontsize * scaling
        if font.is_multibyte():
            wordspace = 0.0

        x, y = self.state.text_object.line_matrix
        need_charspace = False
        vertical = font.is_vertical()

        for segment in segments:
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

                advance = font.char_width(cid) * fontsize * scaling
                if vertical:
                    y += advance
                    if cid == 32 and wordspace:
                        y += wordspace
                else:
                    x += advance
                    if cid == 32 and wordspace:
                        x += wordspace
                need_charspace = True

        self.state.text_object.set_line_matrix((x, y))

    def _matrix_from_operands(self, operands: list[object]) -> Matrix:
        return tuple(self._require_number(operand) for operand in operands)  # type: ignore[return-value]

    def _require_arity(
        self, operator: str, operands: list[object], expected: int
    ) -> None:
        if len(operands) < expected:
            raise ValueError(
                f"{operator} expected {expected} operands, got {len(operands)}."
            )
        if len(operands) > expected:
            # Match pdfminer's stack semantics for malformed content streams:
            # operators consume only the trailing operands they need and leave
            # any leading extras on the argument stack for the next operator.
            if expected == 0:
                self.argstack.extend(operands)
                operands.clear()
            else:
                self.argstack.extend(operands[:-expected])
                del operands[:-expected]

    def _require_name(self, operand: object) -> str:
        if not isinstance(operand, PdfName):
            raise ValueError(f"Expected PdfName operand, got {type(operand)}.")
        return operand.value

    def _require_string(self, operand: object) -> PdfString:
        if not isinstance(operand, PdfString):
            raise ValueError(f"Expected PdfString operand, got {type(operand)}.")
        return operand

    def _require_number(self, operand: object) -> float:
        if not isinstance(operand, int | float):
            raise ValueError(f"Expected numeric operand, got {type(operand)}.")
        return float(operand)

    def _recover_path_numbers(
        self,
        operator: str,
        operands: list[object],
        expected: int,
    ) -> tuple[float, ...] | None:
        if len(operands) < expected:
            numeric_values = [
                self._require_number(operand)
                for operand in operands
                if isinstance(operand, int | float)
            ]
            if len(numeric_values) < expected:
                return None
            return tuple(numeric_values[-expected:])
        self._require_arity(operator, operands, expected)
        numeric_values = [
            self._require_number(operand)
            for operand in operands
            if isinstance(operand, int | float)
        ]
        if len(numeric_values) < expected:
            return None
        return tuple(numeric_values[-expected:])


def interpret_operations(operations: list[PdfOperation]) -> list[object]:
    return TextContentInterpreter().run(operations)
