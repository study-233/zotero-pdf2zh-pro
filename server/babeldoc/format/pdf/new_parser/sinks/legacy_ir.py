from __future__ import annotations

from babeldoc.format.pdf.document_il.frontend.il_creater import ILCreater
from babeldoc.format.pdf.new_parser.interpreter import BeginXObjectEvent
from babeldoc.format.pdf.new_parser.interpreter import ClipPathEvent
from babeldoc.format.pdf.new_parser.interpreter import GraphicStateOpEvent
from babeldoc.format.pdf.new_parser.interpreter import ImageXObjectEvent
from babeldoc.format.pdf.new_parser.interpreter import InlineImageEvent
from babeldoc.format.pdf.new_parser.interpreter import PathPaintEvent
from babeldoc.format.pdf.new_parser.interpreter import RestoreGraphicsStateEvent
from babeldoc.format.pdf.new_parser.interpreter import SaveGraphicsStateEvent
from babeldoc.format.pdf.new_parser.interpreter import TextRunEvent
from babeldoc.format.pdf.new_parser.pdf_token_serializer import serialize_pdf_token
from babeldoc.format.pdf.new_parser.resources import PageResourceBundle
from babeldoc.format.pdf.new_parser.state import apply_matrix_pt
from babeldoc.format.pdf.new_parser.state import multiply_matrices
from babeldoc.format.pdf.new_parser.text_positioning import TextRunPositioner
from babeldoc.format.pdf.new_parser.tokenizer import canonical_pdf_name


class _NativeCurve:
    def __init__(
        self,
        *,
        pts: list[tuple[float, float]],
        stroke: bool,
        fill: bool,
        evenodd: bool,
        transformed_path: list[tuple],
        xobj_id: int,
        render_order: int,
        ctm,
        raw_path: list[tuple],
        original_path_primitive,
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
        self.passthrough_instruction = []
        self.xobj_id = xobj_id
        self.render_order = render_order
        self.ctm = ctm
        self.raw_path = raw_path
        self.original_path_primitive = original_path_primitive
        self.clip_paths = []


class _BufferedItem:
    def __init__(self, kind: str, value) -> None:
        self.kind = kind
        self.value = value


class LegacyIRSink:
    def __init__(self, il_creater: ILCreater) -> None:
        self.il_creater = il_creater

    @property
    def current_page_font_name_id_map(self):
        return self.il_creater.current_page_font_name_id_map

    @property
    def current_clip_paths(self):
        return self.il_creater.current_clip_paths

    @property
    def passthrough_per_char_instruction(self):
        return self.il_creater.passthrough_per_char_instruction

    @property
    def xobj_id(self) -> int:
        return self.il_creater.xobj_id

    @property
    def mupdf(self):
        return self.il_creater.mupdf

    @mupdf.setter
    def mupdf(self, value) -> None:
        self.il_creater.mupdf = value

    def get_render_order_and_increase(self) -> int:
        return self.il_creater.get_render_order_and_increase()

    def on_total_pages(self, total_pages: int) -> None:
        self.il_creater.on_total_pages(total_pages)

    def on_page_start(self) -> None:
        self.il_creater.on_page_start()

    def on_page_media_box(self, x: float, y: float, x2: float, y2: float) -> None:
        self.il_creater.on_page_media_box(x, y, x2, y2)

    def on_page_crop_box(self, x: float, y: float, x2: float, y2: float) -> None:
        self.il_creater.on_page_crop_box(x, y, x2, y2)

    def on_page_number(self, page_number: int) -> None:
        self.il_creater.on_page_number(page_number)

    def on_page_resource_font(self, font, objid, fontid) -> None:
        self.il_creater.on_page_resource_font(font, objid, fontid)

    def apply_native_graphic_state_op(self, event: GraphicStateOpEvent) -> None:
        if event.operator == "d":
            dash = event.args[0] if len(event.args) > 0 else []
            phase = event.args[1] if len(event.args) > 1 else 0
            self.il_creater.on_line_dash(dash, phase)
            return
        args = [self._stringify_graphics_arg(arg) for arg in event.args]
        self.il_creater.on_passthrough_per_char(event.operator, args)

    def push_native_graphics_state(self, _event: SaveGraphicsStateEvent) -> None:
        self.il_creater.push_passthrough_per_char_instruction()

    def pop_native_graphics_state(self, _event: RestoreGraphicsStateEvent) -> None:
        self.il_creater.pop_passthrough_per_char_instruction()

    def register_native_font_resources(
        self,
        resource_bundle: PageResourceBundle,
        *,
        xobject_path: tuple[str, ...],
        emitted_font_keys: set[object],
    ) -> None:
        for font_id, font in resource_bundle.get_direct_font_map(xobject_path).items():
            original_descent = font.descent
            font_key = (
                getattr(font, "xref_id", None),
                getattr(font, "objid", None),
                font_id,
                id(font),
            )
            if font_key not in emitted_font_keys:
                font.descent = getattr(font, "legacy_descent", font.descent)
                emitted_font_keys.add(font_key)
            self.on_page_resource_font(
                font,
                getattr(font, "xobj_id", None),
                canonical_pdf_name(font_id),
            )
            font.descent = original_descent

    def begin_native_root_scope(
        self,
        resource_bundle: PageResourceBundle,
        *,
        emitted_font_keys: set[object],
    ) -> None:
        self.register_native_font_resources(
            resource_bundle,
            xobject_path=(),
            emitted_font_keys=emitted_font_keys,
        )

    def on_xobj_begin(self, bbox, xref_id):
        return self.il_creater.on_xobj_begin(bbox, xref_id)

    def on_xobj_end(self, xobj_id, base_op):
        self.il_creater.on_xobj_end(xobj_id, base_op)

    def on_xobj_form(
        self,
        ctm,
        xobj_id,
        xref_id,
        form_type,
        do_args,
        bbox,
        matrix,
    ) -> None:
        self.il_creater.on_xobj_form(
            ctm,
            xobj_id,
            xref_id,
            form_type,
            do_args,
            bbox,
            matrix,
        )

    def emit_native_inline_image(
        self, event: InlineImageEvent, *, xobj_id: int
    ) -> None:
        _ = xobj_id
        self.il_creater.on_inline_image_begin()
        self.il_creater.on_inline_image_end(event.stream, event.ctm)

    def emit_native_image_xobject(
        self, event: ImageXObjectEvent, *, xobj_id: int
    ) -> None:
        self.on_xobj_form(
            event.ctm,
            xobj_id,
            event.xref_id or -1,
            "image",
            event.name,
            event.bbox,
            event.matrix,
        )

    def emit_native_clip_path(self, event: ClipPathEvent) -> None:
        self.il_creater.on_pdf_clip_path(
            list(event.path),
            event.evenodd,
            event.ctm,
        )

    def begin_native_xobject(
        self,
        event: BeginXObjectEvent,
        *,
        parent_xobj_id: int,
    ) -> int | None:
        if event.subtype == "Form" and event.xref_id:
            self.on_xobj_form(
                event.ctm,
                parent_xobj_id,
                event.xref_id,
                "form",
                event.name,
                event.bbox,
                event.matrix,
            )
            child_bbox = self._transform_bbox(event.matrix, event.ctm, event.bbox)
            return self.on_xobj_begin(child_bbox, event.xref_id)
        return None

    def begin_native_xobject_scope(
        self,
        event: BeginXObjectEvent,
        resource_bundle: PageResourceBundle,
        *,
        parent_xobj_id: int,
        emitted_font_keys: set[object],
    ) -> tuple[str, int | None]:
        if event.subtype == "Form" and event.xref_id:
            child_xobj_id = self.begin_native_xobject(
                event,
                parent_xobj_id=parent_xobj_id,
            )
            self.register_native_font_resources(
                resource_bundle,
                xobject_path=(*event.xobject_path, event.name),
                emitted_font_keys=emitted_font_keys,
            )
            return "form", child_xobj_id
        return "ignore", None

    def end_native_xobject(self, xobj_id: int, base_op: str = " ") -> None:
        self.on_xobj_end(xobj_id, base_op)

    def build_native_curve(
        self,
        event: PathPaintEvent,
        *,
        xobj_id: int,
    ) -> _NativeCurve | None:
        path = list(event.path)
        shape = "".join(segment[0] for segment in path)
        if not shape.startswith("m"):
            return None

        raw_pts = [
            segment[-2:] if segment[0] != "h" else path[0][-2:] for segment in path
        ]
        pts = [apply_matrix_pt(event.ctm, pt) for pt in raw_pts]
        operators = [str(segment[0]) for segment in path]
        transformed_points = [
            [
                apply_matrix_pt(event.ctm, (float(operand1), float(operand2)))
                for operand1, operand2 in zip(
                    segment[1::2],
                    segment[2::2],
                    strict=False,
                )
            ]
            for segment in path
        ]
        transformed_path = [
            (operator, *points)
            for operator, points in zip(operators, transformed_points, strict=False)
        ]

        if len(shape) > 3 and shape[-2:] == "lh" and pts[-2] == pts[0]:
            pts.pop()

        return _NativeCurve(
            pts=pts,
            stroke=event.stroke,
            fill=event.fill,
            evenodd=event.evenodd,
            transformed_path=transformed_path,
            xobj_id=xobj_id,
            render_order=self.get_render_order_and_increase(),
            ctm=event.ctm,
            raw_path=path,
            original_path_primitive=event.original_path_primitive,
        )

    def buffer_native_text_run(
        self,
        event: TextRunEvent,
        resource_bundle: PageResourceBundle,
        *,
        xobj_id: int,
        text_run_positioner: TextRunPositioner,
    ) -> list[_BufferedItem]:
        items: list[_BufferedItem] = []
        for char in text_run_positioner.position_text_run(
            event,
            resource_bundle,
            xobj_id=xobj_id,
        ):
            char.render_order = self.get_render_order_and_increase()
            char.clip_paths = self.current_clip_paths.copy()
            char.graphicstate.passthrough_instruction = (
                self.passthrough_per_char_instruction.copy()
            )
            items.append(_BufferedItem("char", char))
        return items

    def buffer_native_path_paint(
        self,
        event: PathPaintEvent,
        *,
        xobj_id: int,
    ) -> list[_BufferedItem]:
        curve = self.build_native_curve(event, xobj_id=xobj_id)
        if curve is None:
            return []
        curve.clip_paths = self.current_clip_paths.copy()
        curve.passthrough_instruction = self.passthrough_per_char_instruction.copy()
        return [_BufferedItem("curve", curve)]

    def flush_native_buffered_items(self, items: list[_BufferedItem]) -> int:
        for item in items:
            if item.kind == "char":
                self.on_lt_char(item.value)
            elif item.kind == "curve":
                self.on_lt_curve(item.value)
        return len(items)

    def on_lt_char(self, item) -> None:
        original_clip_paths = self.il_creater.current_clip_paths
        item_clip_paths = getattr(item, "clip_paths", None)
        if item_clip_paths is None:
            self.il_creater.on_lt_char(item)
            return
        self.il_creater.current_clip_paths = item_clip_paths
        try:
            self.il_creater.on_lt_char(item)
        finally:
            self.il_creater.current_clip_paths = original_clip_paths

    def on_lt_curve(self, item) -> None:
        self.il_creater.on_lt_curve(item)

    def on_pdf_figure(self, item) -> None:
        self.il_creater.on_pdf_figure(item)

    def on_page_base_operation(self, operation: str) -> None:
        self.il_creater.on_page_base_operation(operation)

    def on_page_end(self) -> None:
        self.il_creater.on_page_end()

    def on_finish(self) -> None:
        self.il_creater.on_finish()

    def create_document(self):
        return self.il_creater.create_il()

    @staticmethod
    def _transform_bbox(
        matrix,
        ctm,
        bbox: tuple[float, float, float, float],
    ) -> tuple[float, float, float, float]:
        combined = multiply_matrices(matrix, ctm)
        corners = (
            (bbox[0], bbox[1]),
            (bbox[2], bbox[1]),
            (bbox[0], bbox[3]),
            (bbox[2], bbox[3]),
        )
        points = [apply_matrix_pt(combined, pt) for pt in corners]
        xs = [pt[0] for pt in points]
        ys = [pt[1] for pt in points]
        return (min(xs), min(ys), max(xs), max(ys))

    @staticmethod
    def _stringify_graphics_arg(arg: object) -> str:
        return serialize_pdf_token(arg)
