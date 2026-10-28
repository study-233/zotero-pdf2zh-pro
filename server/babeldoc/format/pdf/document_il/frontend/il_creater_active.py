"""Active projector for the native PDF parser path.

The legacy `il_creater.py` remains available for compatibility tooling, while
new graphic/path ownership work belongs in this native-projector path.
"""

from __future__ import annotations

import base64
import copy
import functools
import json
import re
import unicodedata
from types import MappingProxyType
from typing import TYPE_CHECKING

import pymupdf
import tiktoken

from babeldoc.format.pdf.babelpdf.base14 import get_base14_bbox
from babeldoc.format.pdf.babelpdf.cidfont import get_cidfont_bbox
from babeldoc.format.pdf.babelpdf.type3 import Type3FontMetrics
from babeldoc.format.pdf.babelpdf.type3 import get_type3_bbox
from babeldoc.format.pdf.babelpdf.type3 import get_type3_font_metrics
from babeldoc.format.pdf.babelpdf.utils import guarded_bbox
from babeldoc.format.pdf.document_il import il_version_1
from babeldoc.format.pdf.document_il.frontend.il_creater_active_support import (
    EMPTY_CLIP_PATH_SNAPSHOT,
)
from babeldoc.format.pdf.document_il.frontend.il_creater_active_support import (
    EMPTY_PASSTHROUGH_SNAPSHOT,
)
from babeldoc.format.pdf.document_il.frontend.il_creater_active_support import (
    ClipPathSnapshot,
)
from babeldoc.format.pdf.document_il.frontend.il_creater_active_support import (
    CtmAwarePassthroughArg,
)
from babeldoc.format.pdf.document_il.frontend.il_creater_active_support import (
    LazyPassthroughInstruction,
)
from babeldoc.format.pdf.document_il.frontend.il_creater_active_support import (
    PassthroughSnapshot,
)
from babeldoc.format.pdf.document_il.frontend.il_creater_active_support import (
    append_clip_path_instruction,
)
from babeldoc.format.pdf.document_il.frontend.il_creater_active_support import (
    append_passthrough_instruction,
)
from babeldoc.format.pdf.document_il.frontend.il_creater_active_support import batched
from babeldoc.format.pdf.document_il.frontend.il_creater_active_support import (
    get_rotation_angle,
)
from babeldoc.format.pdf.document_il.frontend.il_creater_active_support import indirect
from babeldoc.format.pdf.document_il.frontend.il_creater_active_support import logger
from babeldoc.format.pdf.document_il.frontend.il_creater_active_support import (
    parse_cmap,
)
from babeldoc.format.pdf.document_il.frontend.il_creater_active_support import (
    parse_encoding,
)
from babeldoc.format.pdf.document_il.frontend.il_creater_active_support import (
    parse_font_encoding,
)
from babeldoc.format.pdf.document_il.frontend.il_creater_active_support import (
    parse_font_file,
)
from babeldoc.format.pdf.document_il.frontend.il_creater_active_support import (
    remove_latest_passthrough_instruction,
)
from babeldoc.format.pdf.document_il.frontend.il_creater_active_support import (
    render_passthrough_snapshot,
)
from babeldoc.format.pdf.document_il.frontend.il_creater_active_support import (
    replace_first_passthrough_operator,
)
from babeldoc.format.pdf.document_il.frontend.il_creater_active_support import (
    space_regex,
)
from babeldoc.format.pdf.document_il.frontend.inline_image_params import (
    normalize_inline_image_parameters,
)
from babeldoc.format.pdf.document_il.utils import zstd_helper
from babeldoc.format.pdf.document_il.utils.fontmap import FontMapper
from babeldoc.format.pdf.document_il.utils.matrix_helper import decompose_ctm
from babeldoc.format.pdf.document_il.utils.style_helper import BLACK
from babeldoc.format.pdf.document_il.utils.style_helper import YELLOW
from babeldoc.format.pdf.document_il.utils.type3_font_metrics import (
    build_type3_pdf_font_fields,
)
from babeldoc.format.pdf.document_il.utils.type3_font_metrics import (
    effective_type3_font_size,
)
from babeldoc.format.pdf.new_parser.interpreter import BeginXObjectEvent
from babeldoc.format.pdf.new_parser.interpreter import ImageXObjectEvent
from babeldoc.format.pdf.new_parser.interpreter import InlineImageEvent
from babeldoc.format.pdf.new_parser.interpreter import PathPaintEvent
from babeldoc.format.pdf.new_parser.interpreter import ShadingPaintEvent
from babeldoc.format.pdf.new_parser.interpreter import TextRunEvent
from babeldoc.format.pdf.new_parser.pdf_token_serializer import serialize_pdf_token
from babeldoc.format.pdf.new_parser.resources import PageResourceBundle
from babeldoc.format.pdf.new_parser.state import apply_matrix_pt
from babeldoc.format.pdf.new_parser.state import get_bound
from babeldoc.format.pdf.new_parser.state import invert_matrix
from babeldoc.format.pdf.new_parser.state import multiply_matrices
from babeldoc.format.pdf.new_parser.text_positioning import TextRunPositioner
from babeldoc.format.pdf.new_parser.tokenizer import canonical_pdf_name
from babeldoc.format.pdf.translation_config import TranslationConfig

if TYPE_CHECKING:
    from babeldoc.pdfminer import pdfinterp as pdfinterp_module
    from babeldoc.pdfminer.layout import LTChar


LAZY_PASSTHROUGH_RENDER_EVENT_THRESHOLD = 256


class _ActiveNativeCurve:
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
        ctm: tuple[float, ...],
        raw_path: list[tuple],
        original_path_primitive,
    ) -> None:
        self.original_path = transformed_path
        self.pts = pts
        self.stroke = stroke
        self.fill = fill
        self.evenodd = evenodd
        self.xobj_id = xobj_id
        self.render_order = render_order
        self.ctm = ctm
        self.raw_path = raw_path
        self.original_path_primitive = original_path_primitive
        self.bbox = get_bound(pts)
        self.clip_paths: ClipPathSnapshot = EMPTY_CLIP_PATH_SNAPSHOT
        self.passthrough_instruction: PassthroughSnapshot = EMPTY_PASSTHROUGH_SNAPSHOT


class _ActiveNativeShadingPaint:
    def __init__(
        self,
        *,
        name: str,
        xobj_id: int,
        render_order: int,
        ctm: tuple[float, ...],
    ) -> None:
        self.name = name
        self.xobj_id = xobj_id
        self.render_order = render_order
        self.ctm = ctm
        self.clip_paths: ClipPathSnapshot = EMPTY_CLIP_PATH_SNAPSHOT
        self.passthrough_instruction: PassthroughSnapshot = EMPTY_PASSTHROUGH_SNAPSHOT


class _ActiveBufferedItem:
    def __init__(self, kind: str, value) -> None:
        self.kind = kind
        self.value = value


class _ProjectedFontResource:
    def __init__(
        self,
        *,
        metadata: il_version_1.PdfFont,
        bbox_map: dict[int, tuple[float, float, float, float]],
    ) -> None:
        self.metadata = metadata
        self.bbox_map = bbox_map


ProjectedFontCacheKey = tuple[object, ...]


class ActiveILCreater:
    """Active IL creator used by the native parser path.

    The legacy `il_creater.py` remains available for compatibility tooling.
    Native graphic/path ownership work lands here so the product path can evolve
    without mutating the legacy parser implementation.
    """

    stage_name = "Parse PDF and Create Intermediate Representation"

    def __init__(self, translation_config: TranslationConfig):
        self.progress = None
        self.current_page: il_version_1.Page = None
        self.mupdf: pymupdf.Document = None
        self.model = translation_config.doc_layout_model
        self.docs = il_version_1.Document(page=[])
        self.stroking_color_space_name = None
        self.non_stroking_color_space_name = None
        self.passthrough_per_char_instruction: PassthroughSnapshot = (
            EMPTY_PASSTHROUGH_SNAPSHOT
        )
        self.translation_config = translation_config
        self._font_parse_cache = {}
        self.font_parse_count = 0
        self.font_cache_hits = 0
        self.passthrough_per_char_instruction_stack: list[PassthroughSnapshot] = []
        self.xobj_id = 0
        self.xobj_inc = 0
        self.xobj_map: dict[int, il_version_1.PdfXobject] = {}
        self.xobj_stack = []
        self.current_page_font_name_id_map = {}
        self.current_page_font_char_bounding_box_map = {}
        self.current_available_fonts = {}
        self.projected_font_resource_cache: dict[
            ProjectedFontCacheKey,
            _ProjectedFontResource,
        ] = {}
        self.mupdf_font_map: dict[int, pymupdf.Font] = {}
        self.graphic_state_pool = {}
        self.enable_graphic_element_process = (
            translation_config.enable_graphic_element_process
        )
        self.render_order = 0
        self.current_clip_paths: ClipPathSnapshot = EMPTY_CLIP_PATH_SNAPSHOT
        self.clip_paths_stack: list[ClipPathSnapshot] = []
        self.font_mapper = FontMapper(translation_config)
        self.tokenizer = tiktoken.encoding_for_model("gpt-4o")
        self._page_valid_chars_buffer: list[str] | None = None

    def get_render_order_and_increase(self):
        self.render_order += 1
        return self.render_order

    def on_finish(self):
        self.progress.__exit__(None, None, None)

    def create_il(self):
        pages = [
            page
            for page in self.docs.page
            if self.translation_config.should_translate_page(page.page_number + 1)
        ]
        self.docs.page = pages
        return self.docs

    def on_total_pages(self, total_pages: int):
        assert isinstance(total_pages, int)
        assert total_pages > 0
        self.docs.total_pages = total_pages
        total = 0
        for page in range(total_pages):
            if self.translation_config.should_translate_page(page + 1) is False:
                continue
            total += 1
        self.progress = self.translation_config.progress_monitor.stage_start(
            self.stage_name,
            total,
        )

    def transform_clip_path(
        self,
        clip_path,
        source_ctm: tuple[float, float, float, float, float, float],
        target_ctm: tuple[float, float, float, float, float, float],
    ):
        if source_ctm == target_ctm:
            return clip_path
        transform = multiply_matrices(source_ctm, invert_matrix(target_ctm))
        transformed_path = []
        for segment in clip_path:
            if not segment:
                transformed_path.append(segment)
                continue
            op = segment[0]
            coords = segment[1:]
            transformed_coords = []
            for i in range(0, len(coords), 2):
                if i + 1 < len(coords):
                    transformed_coords.extend(
                        apply_matrix_pt(transform, (coords[i], coords[i + 1]))
                    )
                else:
                    transformed_coords.append(coords[i])
            transformed_path.append([op] + transformed_coords)
        return transformed_path

    def get_render_order(self):
        return self.render_order

    def is_graphic_operation(self, operator: str):
        if not self.enable_graphic_element_process:
            return False
        return re.match(
            "^(m|l|c|v|y|re|h|S|s|f|f*|F|B|B*|b|b*|n|Do)$",
            operator,
        )

    def is_passthrough_per_char_operation(self, operator: str):
        return re.match(
            "^(sc|SC|scn|SCN|g|G|rg|RG|k|K|cs|CS|gs|ri|w|J|j|M|i)$",
            operator,
        )

    def can_remove_old_passthrough_per_char_instruction(self, operator: str):
        return re.match(
            "^(sc|SC|scn|SCN|g|G|rg|RG|k|K|cs|CS|ri|w|J|j|M|i|d)$",
            operator,
        )

    def remove_latest_passthrough_per_char_instruction(self):
        self.passthrough_per_char_instruction = remove_latest_passthrough_instruction(
            self.passthrough_per_char_instruction
        )

    def parse_arg(self, arg: str):
        if isinstance(arg, CtmAwarePassthroughArg):
            return arg
        return serialize_pdf_token(arg)

    def on_stroking_color_space(self, color_space_name):
        self.stroking_color_space_name = color_space_name

    def on_non_stroking_color_space(self, color_space_name):
        self.non_stroking_color_space_name = color_space_name

    def on_new_stream(self):
        self.stroking_color_space_name = None
        self.non_stroking_color_space_name = None
        self.passthrough_per_char_instruction = EMPTY_PASSTHROUGH_SNAPSHOT
        self.current_clip_paths = EMPTY_CLIP_PATH_SNAPSHOT

    def on_xobj_begin(self, bbox, xref_id):
        logger.debug(f"on_xobj_begin: {bbox} @ {xref_id}")
        self.push_xobj()
        self.passthrough_per_char_instruction = EMPTY_PASSTHROUGH_SNAPSHOT
        self.passthrough_per_char_instruction_stack = []
        self.clip_paths_stack = []
        self.xobj_inc += 1
        self.xobj_id = self.xobj_inc
        xobject = il_version_1.PdfXobject(
            box=il_version_1.Box(
                x=float(bbox[0]),
                y=float(bbox[1]),
                x2=float(bbox[2]),
                y2=float(bbox[3]),
            ),
            xobj_id=self.xobj_id,
            xref_id=xref_id,
            pdf_font=[],
        )
        self.current_page.pdf_xobject.append(xobject)
        self.xobj_map[self.xobj_id] = xobject
        xobject.pdf_font.extend(self.current_available_fonts.values())
        return self.xobj_id

    def on_xobj_end(self, xobj_id, base_op):
        self.pop_xobj()
        xobj = self.xobj_map[xobj_id]
        base_op = zstd_helper.zstd_compress(base_op)
        xobj.base_operations = il_version_1.BaseOperations(value=base_op)
        self.xobj_inc += 1

    def on_page_start(self):
        self.current_page = il_version_1.Page(
            pdf_font=[],
            pdf_character=[],
            page_layout=[],
            pdf_curve=[],
            pdf_form=[],
            unit="point",
        )
        self.current_page_font_name_id_map = {}
        self.current_page_font_char_bounding_box_map = {}
        self.passthrough_per_char_instruction_stack = []
        self.xobj_stack = []
        self.non_stroking_color_space_name = None
        self.stroking_color_space_name = None
        self.passthrough_per_char_instruction = EMPTY_PASSTHROUGH_SNAPSHOT
        self.current_clip_paths = EMPTY_CLIP_PATH_SNAPSHOT
        self.clip_paths_stack = []
        self.docs.page.append(self.current_page)
        self._page_valid_chars_buffer = []

    def on_page_end(self):
        try:
            if (
                self._page_valid_chars_buffer is not None
                and len(self._page_valid_chars_buffer) > 0
            ):
                page_text = "".join(self._page_valid_chars_buffer)
                char_count = len(page_text)
                try:
                    token_count = len(
                        self.tokenizer.encode(page_text, disallowed_special=())
                    )
                except Exception as e:
                    logger.warning("Failed to compute token count for page: %s", e)
                    token_count = 0
                self.translation_config.shared_context_cross_split_part.add_valid_counts(
                    char_count, token_count
                )
        except Exception as e:
            logger.warning("Failed to accumulate page valid stats: %s", e)
        finally:
            self._page_valid_chars_buffer = []
        self.progress.advance(1)

    def on_page_crop_box(
        self,
        x0: float | int,
        y0: float | int,
        x1: float | int,
        y1: float | int,
    ):
        box = il_version_1.Box(x=float(x0), y=float(y0), x2=float(x1), y2=float(y1))
        self.current_page.cropbox = il_version_1.Cropbox(box=box)

    def on_page_media_box(
        self,
        x0: float | int,
        y0: float | int,
        x1: float | int,
        y1: float | int,
    ):
        box = il_version_1.Box(x=float(x0), y=float(y0), x2=float(x1), y2=float(y1))
        self.current_page.mediabox = il_version_1.Mediabox(box=box)

    def on_page_number(self, page_number: int):
        assert isinstance(page_number, int)
        assert page_number >= 0
        self.current_page.page_number = page_number

    def on_page_base_operation(self, operation: str):
        operation = zstd_helper.zstd_compress(operation)
        self.current_page.base_operations = il_version_1.BaseOperations(value=operation)

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
                font.runtime_identity(),
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

    def emit_native_inline_image(
        self, event: InlineImageEvent, *, xobj_id: int
    ) -> None:
        _ = xobj_id
        self.on_inline_image_begin()
        self.on_inline_image_end(event.stream, event.ctm)

    def emit_native_image_xobject(
        self, event: ImageXObjectEvent, *, xobj_id: int
    ) -> None:
        text_clip_passthrough = self._build_text_clip_passthrough_for_image(event)
        self.on_xobj_form(
            event.ctm,
            xobj_id,
            event.xref_id or -1,
            "image",
            event.name,
            event.bbox,
            event.matrix,
            extra_passthrough_instruction=text_clip_passthrough,
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

    def push_xobj(self):
        self.xobj_stack.append(
            (
                self.xobj_id,
                self.current_clip_paths,
                self.current_available_fonts.copy(),
                self.passthrough_per_char_instruction,
                list(self.passthrough_per_char_instruction_stack),
                list(self.clip_paths_stack),
            ),
        )
        self.current_clip_paths = EMPTY_CLIP_PATH_SNAPSHOT

    def pop_xobj(self):
        (
            self.xobj_id,
            self.current_clip_paths,
            self.current_available_fonts,
            self.passthrough_per_char_instruction,
            self.passthrough_per_char_instruction_stack,
            self.clip_paths_stack,
        ) = self.xobj_stack.pop()

    def push_native_graphics_state(self, _event=None):
        self.push_passthrough_per_char_instruction()

    def pop_native_graphics_state(self, _event=None):
        self.pop_passthrough_per_char_instruction()

    def on_page_resource_font(self, font, xref_id: int, font_id: str):
        projected_font = self._project_font_resource(
            font=font,
            xref_id=xref_id,
            font_id=font_id,
        )

        self._record_font_bbox_map(
            xref_id=xref_id,
            bbox_map=projected_font.bbox_map,
        )
        self._install_projected_font(
            font_id=font_id,
            xref_id=xref_id,
            metadata=projected_font.metadata,
        )

    def _project_font_resource(
        self,
        *,
        font,
        xref_id: int,
        font_id: str,
    ) -> _ProjectedFontResource:
        cache_key = self._projected_font_resource_cache_key(
            font=font,
            xref_id=xref_id,
            font_id=font_id,
        )
        cached = self.projected_font_resource_cache.get(cache_key)
        if cached is not None:
            return self._clone_projected_font_resource_template(cached)

        font_name = self._decode_font_name(font.fontname)
        font_subtype = self._get_font_subtype(xref_id)
        type3_metrics = (
            get_type3_font_metrics(self.mupdf, xref_id)
            if font_subtype == "Type3"
            else None
        )
        type3_fields = build_type3_pdf_font_fields(
            font_subtype=font_subtype,
            metrics=type3_metrics,
            fallback_ascent=font.ascent,
            fallback_descent=font.descent,
        )
        il_font_metadata = il_version_1.PdfFont(
            name=font_name,
            xref_id=xref_id,
            font_id=font_id,
            encoding_length=self._compute_font_encoding_length(
                font=font,
                xref_id=xref_id,
            ),
            **type3_fields,
            pdf_font_char_bounding_box=[],
            **self._compute_font_style_flags(xref_id),
        )
        bbox_map = self._populate_font_bounding_boxes(
            metadata=il_font_metadata,
            xref_id=xref_id,
            type3_metrics=type3_metrics,
        )
        projected = _ProjectedFontResource(
            metadata=il_font_metadata,
            bbox_map=bbox_map,
        )
        self.projected_font_resource_cache[cache_key] = (
            self._clone_projected_font_resource_template(projected)
        )
        return projected

    def _projected_font_resource_cache_key(
        self,
        *,
        font,
        xref_id: int,
        font_id: str,
    ) -> ProjectedFontCacheKey:
        return (
            xref_id,
            getattr(font, "xobj_id", None),
            getattr(font, "objid", None),
            font_id,
            font.runtime_identity(),
            font.fontname,
            font.ascent,
            font.descent,
        )

    def _clone_projected_font_resource_template(
        self,
        projected: _ProjectedFontResource,
    ) -> _ProjectedFontResource:
        metadata = copy.copy(projected.metadata)
        metadata.pdf_font_char_bounding_box = (
            projected.metadata.pdf_font_char_bounding_box
        )
        return _ProjectedFontResource(
            metadata=metadata,
            bbox_map=projected.bbox_map,
        )

    def _decode_font_name(self, font_name: str | bytes) -> str:
        logger.debug(f"handle font {font_name} in {self.xobj_id}")
        if isinstance(font_name, bytes):
            try:
                font_name = font_name.decode("utf-8")
            except UnicodeDecodeError:
                font_name = "BASE64:" + base64.b64encode(font_name).decode("utf-8")
        return font_name

    def _compute_font_encoding_length(self, *, font, xref_id: int) -> int:
        compute_encoding_length = getattr(font, "compute_encoding_length", None)
        if not callable(compute_encoding_length):
            msg = (
                "ActiveILCreater expected a runtime font with "
                "compute_encoding_length(...), got "
                f"{type(font).__name__}"
            )
            raise TypeError(msg)
        return compute_encoding_length(mupdf=self.mupdf, xref_id=xref_id)

    def _get_font_subtype(self, xref_id: int) -> str | None:
        try:
            _kind, value = self.mupdf.xref_get_key(xref_id, "Subtype")
        except Exception:
            return None
        if not value:
            return None
        return value[1:] if value.startswith("/") else value

    def _compute_font_style_flags(self, xref_id: int) -> dict[str, bool | None]:
        try:
            if xref_id in self.mupdf_font_map:
                mupdf_font = self.mupdf_font_map[xref_id]
            else:
                mupdf_font = pymupdf.Font(
                    fontbuffer=self.mupdf.extract_font(xref_id)[3]
                )
                mupdf_font.has_glyph = functools.lru_cache(maxsize=10240, typed=True)(
                    mupdf_font.has_glyph,
                )
            bold = mupdf_font.is_bold
            italic = mupdf_font.is_italic
            monospaced = mupdf_font.is_monospaced
            serif = mupdf_font.is_serif
            self.mupdf_font_map[xref_id] = mupdf_font
        except Exception:
            bold = None
            italic = None
            monospaced = None
            serif = None
        return {
            "bold": bold,
            "italic": italic,
            "monospace": monospaced,
            "serif": serif,
        }

    def _populate_font_bounding_boxes(
        self,
        *,
        metadata: il_version_1.PdfFont,
        xref_id: int,
        type3_metrics: Type3FontMetrics | None = None,
    ) -> dict[int, tuple[float, float, float, float]]:
        font_char_bounding_box_map: dict[int, tuple[float, float, float, float]] = {}
        try:
            if xref_id is None:
                logger.warning("xref_id is None for font %s", metadata.name)
                raise ValueError(f"xref_id is None for font {metadata.name}")
            bbox_list, cmap = self.parse_font_xobj_id(
                xref_id,
                type3_metrics=type3_metrics,
            )
            if not cmap:
                cmap = {x: x for x in range(257)}
            for char_id, char_bbox in enumerate(bbox_list):
                font_char_bounding_box_map[char_id] = char_bbox
            for char_id in cmap:
                if char_id < 0 or char_id >= len(bbox_list):
                    continue
                bbox = bbox_list[char_id]
                x, y, x2, y2 = bbox
                if (
                    x == 0
                    and y == 0
                    and x2 == 500
                    and y2 == 698
                    or x == 0
                    and y == 0
                    and x2 == 0
                    and y2 == 0
                ):
                    continue
                metadata.pdf_font_char_bounding_box.append(
                    il_version_1.PdfFontCharBoundingBox(
                        x=x,
                        y=y,
                        x2=x2,
                        y2=y2,
                        char_id=char_id,
                    )
                )
                font_char_bounding_box_map[char_id] = bbox
        except Exception as e:
            if xref_id is None:
                logger.error("failed to parse font xobj id None: %s", e)
            else:
                logger.error("failed to parse font xobj id %d: %s", xref_id, e)
        return font_char_bounding_box_map

    def _record_font_bbox_map(
        self,
        *,
        xref_id: int,
        bbox_map: dict[int, tuple[float, float, float, float]],
    ) -> None:
        if self.xobj_id in self.xobj_map:
            if self.xobj_id not in self.current_page_font_char_bounding_box_map:
                self.current_page_font_char_bounding_box_map[self.xobj_id] = {}
            self.current_page_font_char_bounding_box_map[self.xobj_id][xref_id] = (
                bbox_map
            )
        else:
            self.current_page_font_char_bounding_box_map[xref_id] = bbox_map

    def _install_projected_font(
        self,
        *,
        font_id: str,
        xref_id: int,
        metadata: il_version_1.PdfFont,
    ) -> None:
        self.current_page_font_name_id_map[xref_id] = font_id
        self.current_available_fonts[font_id] = metadata

        fonts = self.current_page.pdf_font
        if self.xobj_id in self.xobj_map:
            fonts = self.xobj_map[self.xobj_id].pdf_font
        should_remove = []
        for f in fonts:
            if f.font_id == font_id:
                should_remove.append(f)
        for sr in should_remove:
            fonts.remove(sr)
        fonts.append(metadata)

    def report_operation(self, operation, **detail):
        self.translation_config.raise_if_cancelled()
        callback = getattr(self.translation_config, "report_parse_progress", None)
        if callback:
            callback(operation, fontParseCount=self.font_parse_count,
                     fontCacheHits=self.font_cache_hits, **detail)

    def parse_font_xobj_id(self, xobj_id: int, *, type3_metrics=None):
        self.translation_config.raise_if_cancelled()
        if xobj_id is None:
            return (), MappingProxyType({})
        if xobj_id in self._font_parse_cache:
            self.font_cache_hits += 1
            return self._font_parse_cache[xobj_id]
        self.report_operation("font", fontObject=xobj_id)
        boxes, cmap = self._parse_font_xobj_uncached(xobj_id, type3_metrics=type3_metrics)
        # Owned by this document only. Consumers must not mutate shared values.
        result = (tuple(tuple(box) for box in boxes), MappingProxyType(dict(cmap)))
        self._font_parse_cache[xobj_id] = result
        self.font_parse_count += 1
        return result

    def _parse_font_xobj_uncached(self, xobj_id: int, *, type3_metrics=None):
        if xobj_id is None:
            return [], {}

        bbox_list = []
        encoding = parse_font_encoding(self.mupdf, xobj_id)
        differences = []
        font_differences = self.mupdf.xref_get_key(xobj_id, "Encoding/Differences")
        if font_differences:
            differences = parse_encoding(font_differences[1])
        for file_key in ["FontFile", "FontFile2", "FontFile3"]:
            font_file = self.mupdf.xref_get_key(xobj_id, f"FontDescriptor/{file_key}")
            if file_idx := indirect(font_file):
                bbox_list = parse_font_file(
                    self.mupdf,
                    file_idx,
                    encoding,
                    differences,
                )
        cmap = {}
        to_unicode = self.mupdf.xref_get_key(xobj_id, "ToUnicode")
        if to_unicode_idx := indirect(to_unicode):
            cmap = parse_cmap(self.mupdf.xref_stream(to_unicode_idx).decode("U8"))
        if not bbox_list:
            obj_type, obj_val = self.mupdf.xref_get_key(xobj_id, "BaseFont")
            if obj_type == "name":
                bbox_list = get_base14_bbox(obj_val[1:], check_cancelled=self.translation_config.raise_if_cancelled)
        if cid_bbox := get_cidfont_bbox(self.mupdf, xobj_id, self.translation_config.raise_if_cancelled):
            bbox_list = cid_bbox
        if self.mupdf.xref_get_key(xobj_id, "Subtype")[1] == "/Type3":
            bbox_list = get_type3_bbox(
                self.mupdf,
                xobj_id,
                normalize_to_1000_em=True,
                metrics=type3_metrics,
                check_cancelled=self.translation_config.raise_if_cancelled,
            )
        return bbox_list, cmap

    def on_line_dash(self, dash, phase):
        dash_str = f"[{' '.join(f'{arg}' for arg in dash)}]"
        self.on_passthrough_per_char("d", [dash_str, str(phase)])

    def on_passthrough_per_char(self, operator: str, args: list[str]):
        if not self.is_passthrough_per_char_operation(operator) and operator not in (
            "W n",
            "W* n",
            "d",
            "W",
            "W*",
        ):
            logger.error("Unknown passthrough_per_char operation: %s", operator)
            return
        args = [self.parse_arg(arg) for arg in args]
        instruction_args = (
            args[0] if len(args) == 1 else " ".join(str(arg) for arg in args)
        )
        if self.can_remove_old_passthrough_per_char_instruction(operator):
            self.passthrough_per_char_instruction = replace_first_passthrough_operator(
                self.passthrough_per_char_instruction,
                operator,
                (operator, instruction_args),
            )
            return
        self.passthrough_per_char_instruction = append_passthrough_instruction(
            self.passthrough_per_char_instruction,
            (operator, instruction_args),
        )

    def pop_passthrough_per_char_instruction(self):
        if self.passthrough_per_char_instruction_stack:
            self.passthrough_per_char_instruction = (
                self.passthrough_per_char_instruction_stack.pop()
            )
        else:
            self.passthrough_per_char_instruction = EMPTY_PASSTHROUGH_SNAPSHOT
            logger.error(
                "pop_passthrough_per_char_instruction error on page: %s",
                self.current_page.page_number,
            )

        if self.clip_paths_stack:
            self.current_clip_paths = self.clip_paths_stack.pop()
        else:
            self.current_clip_paths = EMPTY_CLIP_PATH_SNAPSHOT

    def push_passthrough_per_char_instruction(self):
        self.passthrough_per_char_instruction_stack.append(
            self.passthrough_per_char_instruction,
        )
        self.clip_paths_stack.append(self.current_clip_paths)

    def on_pdf_clip_path(
        self,
        clip_path,
        evenodd: bool,
        ctm: tuple[float, float, float, float, float, float],
    ):
        try:
            self.current_clip_paths = append_clip_path_instruction(
                self.current_clip_paths,
                clip_path,
                ctm,
                evenodd,
            )
        except Exception as e:
            logger.warning("Error in on_pdf_clip_path: %s", e)

    def emit_native_clip_path(self, event):
        self.on_pdf_clip_path(list(event.path), event.evenodd, event.ctm)

    def apply_native_graphic_state_op(self, event):
        if event.operator == "d":
            dash = event.args[0] if len(event.args) > 0 else []
            phase = event.args[1] if len(event.args) > 1 else 0
            self.on_line_dash(dash, phase)
            return
        args = [self.parse_arg(arg) for arg in event.args]
        if event.operator == "gs" and args:
            args[0] = CtmAwarePassthroughArg(args[0], tuple(event.ctm))
        self.on_passthrough_per_char(event.operator, args)

    def create_graphic_state(
        self,
        gs: pdfinterp_module.PDFGraphicState | list[tuple[str, str]],
        include_clipping: bool = False,
        target_ctm: tuple[float, float, float, float, float, float] = None,
        clip_paths=None,
        extra_passthrough_instruction: str | None = None,
        preserve_extgstate_ctm: bool = False,
    ):
        if clip_paths is None:
            clip_paths = self.current_clip_paths
        passthrough_instruction = getattr(gs, "passthrough_instruction", gs)

        passthrough_per_char_instruction_parts = []
        can_lazy_render = (
            isinstance(passthrough_instruction, PassthroughSnapshot)
            and passthrough_instruction.event_count
            > LAZY_PASSTHROUGH_RENDER_EVENT_THRESHOLD
            and not preserve_extgstate_ctm
        )
        if not can_lazy_render:
            base_instruction = render_passthrough_snapshot(
                passthrough_instruction,
                include_clipping=include_clipping,
                target_ctm_for_extgstate=(
                    tuple(target_ctm)
                    if preserve_extgstate_ctm and target_ctm is not None
                    else None
                ),
            )
            if base_instruction:
                passthrough_per_char_instruction_parts.append(base_instruction)

        if include_clipping and target_ctm and clip_paths:
            for clip_path, source_ctm, evenodd in clip_paths:
                try:
                    transformed_path = self.transform_clip_path(
                        clip_path, source_ctm, target_ctm
                    )

                    op = "W* n" if evenodd else "W n"
                    args = []
                    for p in transformed_path:
                        if len(p) == 1:
                            args.append(p[0])
                        elif len(p) > 1:
                            args.extend([f"{x:F}" for x in p[1:]])
                            args.append(p[0])

                    if args:
                        clipping_instruction = f"{' '.join(args)} {op}"
                        passthrough_per_char_instruction_parts.append(
                            clipping_instruction
                        )

                except Exception as e:
                    logger.warning("Error transforming clip path: %s", e)

        if extra_passthrough_instruction:
            passthrough_per_char_instruction_parts.append(extra_passthrough_instruction)

        if can_lazy_render:
            passthrough_per_char_instruction = LazyPassthroughInstruction(
                passthrough_instruction,
                include_clipping=include_clipping,
                suffix_parts=passthrough_per_char_instruction_parts,
            )
            pool_key = (
                "lazy",
                id(passthrough_instruction),
                include_clipping,
                tuple(passthrough_per_char_instruction_parts),
                preserve_extgstate_ctm,
            )
        else:
            passthrough_per_char_instruction = " ".join(
                passthrough_per_char_instruction_parts
            )
            pool_key = passthrough_per_char_instruction

        if pool_key not in self.graphic_state_pool:
            self.graphic_state_pool[pool_key] = il_version_1.GraphicState(
                passthrough_per_char_instruction=passthrough_per_char_instruction
            )
        graphic_state = self.graphic_state_pool[pool_key]

        return graphic_state

    def build_native_curve(
        self,
        event: PathPaintEvent,
        *,
        xobj_id: int,
    ):
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

        return _ActiveNativeCurve(
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
    ):
        items = []
        for char in text_run_positioner.position_text_run(
            event,
            resource_bundle,
            xobj_id=xobj_id,
        ):
            char.render_order = self.get_render_order_and_increase()
            char.clip_paths = self.current_clip_paths
            char.graphicstate.passthrough_instruction = (
                self.passthrough_per_char_instruction
            )
            items.append(_ActiveBufferedItem("char", char))
        return items

    def buffer_native_path_paint(
        self,
        event: PathPaintEvent,
        *,
        xobj_id: int,
    ):
        curve = self.build_native_curve(event, xobj_id=xobj_id)
        if curve is None:
            return []
        curve.clip_paths = self.current_clip_paths
        curve.passthrough_instruction = self.passthrough_per_char_instruction
        return [_ActiveBufferedItem("curve", curve)]

    def buffer_native_shading_paint(
        self,
        event: ShadingPaintEvent,
        *,
        xobj_id: int,
    ):
        paint = _ActiveNativeShadingPaint(
            name=self.parse_arg(event.name),
            xobj_id=xobj_id,
            render_order=self.get_render_order_and_increase(),
            ctm=event.ctm,
        )
        paint.clip_paths = self.current_clip_paths
        paint.passthrough_instruction = self.passthrough_per_char_instruction
        return [_ActiveBufferedItem("shading", paint)]

    def flush_native_buffered_items(self, items) -> int:
        for item in items:
            kind = getattr(item, "kind", None)
            value = getattr(item, "value", item)
            if kind == "char":
                original_clip_paths = self.current_clip_paths
                item_clip_paths = getattr(value, "clip_paths", None)
                if item_clip_paths is None:
                    self.project_native_char(value)
                    continue
                self.current_clip_paths = item_clip_paths
                try:
                    self.project_native_char(value)
                finally:
                    self.current_clip_paths = original_clip_paths
            elif kind == "curve":
                self.project_native_curve(value)
            elif kind == "shading":
                self.project_native_shading_paint(value)
        return len(items)

    def project_native_curve(self, curve) -> None:
        if not self.enable_graphic_element_process:
            return
        bbox = il_version_1.Box(
            x=curve.bbox[0],
            y=curve.bbox[1],
            x2=curve.bbox[2],
            y2=curve.bbox[3],
        )
        curve_ctm = getattr(curve, "ctm", None)
        gs = self.create_graphic_state(
            curve.passthrough_instruction,
            include_clipping=True,
            target_ctm=curve_ctm,
            clip_paths=curve.clip_paths,
        )
        paths = []
        for point in curve.original_path:
            op = point[0]
            if len(point) == 1:
                paths.append(
                    il_version_1.PdfPath(
                        op=op,
                        x=None,
                        y=None,
                        has_xy=False,
                    )
                )
                continue
            for p in point[1:-1]:
                paths.append(
                    il_version_1.PdfPath(
                        op="",
                        x=p[0],
                        y=p[1],
                        has_xy=True,
                    )
                )
            paths.append(
                il_version_1.PdfPath(
                    op=point[0],
                    x=point[-1][0],
                    y=point[-1][1],
                    has_xy=True,
                )
            )

        raw_pdf_paths = None
        raw_path = getattr(curve, "raw_path", None)
        if raw_path is not None:
            raw_pdf_paths = []
            for path in raw_path:
                if path[0] == "h":
                    raw_pdf_paths.append(
                        il_version_1.PdfOriginalPath(
                            pdf_path=il_version_1.PdfPath(
                                x=0.0,
                                y=0.0,
                                op=path[0],
                                has_xy=False,
                            )
                        )
                    )
                else:
                    for p in batched(path[1:-2], 2, strict=True):
                        raw_pdf_paths.append(
                            il_version_1.PdfOriginalPath(
                                pdf_path=il_version_1.PdfPath(
                                    x=float(p[0]),
                                    y=float(p[1]),
                                    op="",
                                    has_xy=True,
                                )
                            )
                        )
                    raw_pdf_paths.append(
                        il_version_1.PdfOriginalPath(
                            pdf_path=il_version_1.PdfPath(
                                x=float(path[-2]),
                                y=float(path[-1]),
                                op=path[0],
                                has_xy=True,
                            )
                        )
                    )

        original_path_primitive = None
        if (
            getattr(curve, "original_path_primitive", None) is not None
            and curve.original_path_primitive[0] == "re"
        ):
            original_path_primitive = il_version_1.PdfOriginalPathPrimitive(
                op="re",
                args=[float(arg) for arg in curve.original_path_primitive[1]],
            )

        curve_obj = il_version_1.PdfCurve(
            box=bbox,
            graphic_state=gs,
            pdf_path=paths,
            fill_background=curve.fill,
            stroke_path=curve.stroke,
            evenodd=curve.evenodd,
            debug_info="a",
            xobj_id=curve.xobj_id,
            render_order=curve.render_order,
            ctm=list(curve_ctm) if curve_ctm is not None else None,
            pdf_original_path=raw_pdf_paths,
            pdf_original_path_primitive=original_path_primitive,
        )
        self.current_page.pdf_curve.append(curve_obj)

    def project_native_shading_paint(self, paint) -> None:
        if not self.enable_graphic_element_process:
            return
        gs = self.create_graphic_state(
            paint.passthrough_instruction,
            include_clipping=True,
            target_ctm=paint.ctm,
            clip_paths=paint.clip_paths,
            extra_passthrough_instruction=f"{paint.name} sh",
            preserve_extgstate_ctm=True,
        )
        curve_obj = il_version_1.PdfCurve(
            box=il_version_1.Box(x=0.0, y=0.0, x2=0.0, y2=0.0),
            graphic_state=gs,
            pdf_path=[],
            fill_background=False,
            stroke_path=False,
            evenodd=False,
            passthrough_paint=True,
            debug_info=True,
            xobj_id=paint.xobj_id,
            render_order=paint.render_order,
            ctm=list(paint.ctm),
        )
        self.current_page.pdf_curve.append(curve_obj)

    def project_native_char(self, char: LTChar) -> None:
        if char.aw_font_id is None:
            return
        try:
            rotation_angle = get_rotation_angle(char.matrix)
            if not (-0.1 <= rotation_angle <= 0.1 or 89.9 <= rotation_angle <= 90.1):
                return
        except Exception:
            logger.warning(
                "Failed to get rotation angle for char %s",
                char.get_text(),
            )
        try:
            self._collect_valid_char(char.get_text())
        except Exception as e:
            logger.warning("Error collecting valid char: %s", e)
        gs = self.create_graphic_state(char.graphicstate)
        font = None
        pdf_font = None
        for pdf_font in self.xobj_map.get(char.xobj_id, self.current_page).pdf_font:
            if pdf_font.font_id == char.aw_font_id:
                font = pdf_font
                break

        font_size = effective_type3_font_size(font, char.size)

        descent = 0
        if font and hasattr(font, "descent"):
            descent = font.descent * font_size / 1000

        char_id = char.cid

        char_bounding_box = None
        try:
            if (
                font_bounding_box_map
                := self.current_page_font_char_bounding_box_map.get(
                    char.xobj_id, self.current_page_font_char_bounding_box_map
                ).get(font.xref_id)
            ):
                char_bounding_box = font_bounding_box_map.get(char_id, None)
            else:
                char_bounding_box = None
        except Exception:
            char_bounding_box = None

        char_unicode = char.get_text()
        if space_regex.match(char_unicode):
            char_unicode = " "
        advance = char.adv
        bbox = il_version_1.Box(
            x=char.bbox[0],
            y=char.bbox[1],
            x2=char.bbox[2],
            y2=char.bbox[3],
        )
        if bbox.x2 < bbox.x or bbox.y2 < bbox.y:
            logger.warning(
                "Invalid bounding box for character %s: %s",
                char_unicode,
                bbox,
            )

        if char.matrix[0] == 0 and char.matrix[3] == 0:
            vertical = True
            visual_bbox = il_version_1.Box(
                x=char.bbox[0] - descent,
                y=char.bbox[1],
                x2=char.bbox[2] - descent,
                y2=char.bbox[3],
            )
        else:
            vertical = False
            visual_bbox = il_version_1.Box(
                x=char.bbox[0],
                y=char.bbox[1] + descent,
                x2=char.bbox[2],
                y2=char.bbox[3] + descent,
            )
        visual_bbox = il_version_1.VisualBbox(box=visual_bbox)
        pdf_style = il_version_1.PdfStyle(
            font_id=char.aw_font_id,
            font_size=font_size,
            graphic_state=gs,
        )

        if font:
            font_xref_id = font.xref_id
            if font_xref_id in self.mupdf_font_map:
                _mupdf_font = self.mupdf_font_map[font_xref_id]

        pdf_char = il_version_1.PdfCharacter(
            box=bbox,
            pdf_character_id=char_id,
            advance=advance,
            char_unicode=char_unicode,
            vertical=vertical,
            pdf_style=pdf_style,
            xobj_id=char.xobj_id,
            visual_bbox=visual_bbox,
            render_order=char.render_order,
            sub_render_order=0,
        )
        if self.translation_config.ocr_workaround:
            pdf_char.pdf_style.graphic_state = BLACK
            pdf_char.render_order = None
        if pdf_style.font_size == 0.0:
            logger.warning(
                "Font size is 0.0 for character %s. Skip it.",
                char_unicode,
            )
            return

        if char_bounding_box and len(char_bounding_box) == 4:
            x_min, y_min, x_max, y_max = char_bounding_box
            factor = 1 / 1000 * pdf_style.font_size
            x_min = x_min * factor
            y_min = y_min * factor
            x_max = x_max * factor
            y_max = y_max * factor
            ll = (char.bbox[0] + x_min, char.bbox[1] + y_min)
            ur = (char.bbox[0] + x_max, char.bbox[1] + y_max)

            volume = (ur[0] - ll[0]) * (ur[1] - ll[1])
            if volume > 1:
                pdf_char.visual_bbox = il_version_1.VisualBbox(
                    il_version_1.Box(ll[0], ll[1], ur[0], ur[1])
                )

        self.current_page.pdf_character.append(pdf_char)

    def on_lt_char(self, char: LTChar):
        before = len(self.current_page.pdf_character)
        self.project_native_char(char)
        if len(self.current_page.pdf_character) == before:
            return
        if self.translation_config.show_char_box:
            pdf_char = self.current_page.pdf_character[-1]
            self.current_page.pdf_rectangle.append(
                il_version_1.PdfRectangle(
                    box=pdf_char.visual_bbox.box,
                    graphic_state=YELLOW,
                    debug_info=True,
                    line_width=0.2,
                )
            )

    def _collect_valid_char(self, ch: str):
        if self._page_valid_chars_buffer is None:
            return
        if space_regex.match(ch):
            self._page_valid_chars_buffer.append(ch)
            return
        try:
            cat = unicodedata.category(ch[0]) if ch else None
        except Exception:
            cat = None
        if cat in {"Cc", "Cs", "Co", "Cn"}:
            return
        is_invalid = False
        if not ch:
            is_invalid = True
        elif "(cid:" in ch:
            is_invalid = True
        else:
            try:
                if not self.font_mapper.has_char(ch):
                    if len(ch) > 1 and all(self.font_mapper.has_char(x) for x in ch):
                        is_invalid = False
                    else:
                        is_invalid = True
            except Exception:
                is_invalid = True
        if not is_invalid:
            self._page_valid_chars_buffer.append(ch)

    def on_lt_curve(self, curve):
        self.project_native_curve(curve)

    def on_xobj_form(
        self,
        ctm: tuple[float, float, float, float, float, float],
        xobj_id: int,
        xref_id: int,
        form_type,
        do_args: str,
        bbox: tuple[float, float, float, float],
        matrix: tuple[float, float, float, float, float, float],
        *,
        extra_passthrough_instruction: str | None = None,
    ):
        matrix = multiply_matrices(matrix, ctm)
        (x, y, w, h) = guarded_bbox(bbox)
        bounds = ((x, y), (x + w, y), (x, y + h), (x + w, y + h))
        bbox = get_bound(apply_matrix_pt(matrix, (p, q)) for (p, q) in bounds)

        gs = self.create_graphic_state(
            self.passthrough_per_char_instruction,
            include_clipping=True,
            target_ctm=ctm,
            extra_passthrough_instruction=extra_passthrough_instruction,
        )

        figure_bbox = il_version_1.Box(
            x=bbox[0],
            y=bbox[1],
            x2=bbox[2],
            y2=bbox[3],
        )
        pdf_matrix = il_version_1.PdfMatrix(
            a=ctm[0],
            b=ctm[1],
            c=ctm[2],
            d=ctm[3],
            e=ctm[4],
            f=ctm[5],
        )
        affine_transform = decompose_ctm(ctm)
        xobj_form = il_version_1.PdfXobjForm(xref_id=xref_id, do_args=do_args)
        pdf_form_subtype = il_version_1.PdfFormSubtype(pdf_xobj_form=xobj_form)
        new_form = il_version_1.PdfForm(
            xobj_id=xobj_id,
            box=figure_bbox,
            pdf_matrix=pdf_matrix,
            graphic_state=gs,
            pdf_affine_transform=affine_transform,
            render_order=self.get_render_order_and_increase(),
            form_type=form_type,
            pdf_form_subtype=pdf_form_subtype,
            ctm=list(ctm),
        )
        self.current_page.pdf_form.append(new_form)

    def _build_text_clip_passthrough_for_image(
        self,
        event: ImageXObjectEvent,
    ) -> str | None:
        operation = event.text_clip_passthrough_operation
        source_ctm = event.text_clip_passthrough_ctm
        if not operation or source_ctm is None:
            return None
        to_clip_ctm = multiply_matrices(source_ctm, invert_matrix(event.ctm))
        back_to_image_ctm = multiply_matrices(event.ctm, invert_matrix(source_ctm))
        return (
            f"{self._format_cm(to_clip_ctm)} cm "
            f"{operation} "
            f"{self._format_cm(back_to_image_ctm)} cm"
        )

    @staticmethod
    def _format_cm(
        matrix: tuple[float, float, float, float, float, float],
    ) -> str:
        return " ".join(f"{value:.6f}" for value in matrix)

    def on_pdf_figure(self, figure) -> None:
        box = il_version_1.Box(
            figure.bbox[0],
            figure.bbox[1],
            figure.bbox[2],
            figure.bbox[3],
        )
        self.current_page.pdf_figure.append(il_version_1.PdfFigure(box=box))

    def on_inline_image_begin(self):
        self._inline_image_state = {
            "ctm": None,
            "parameters": {},
        }

    def on_inline_image_end(self, stream_obj, ctm):
        image_dict = stream_obj.attrs if hasattr(stream_obj, "attrs") else {}

        parameters = normalize_inline_image_parameters(image_dict)

        image_data = ""
        if hasattr(stream_obj, "data") and stream_obj.data is not None:
            image_data = base64.b64encode(stream_obj.data).decode("ascii")
        elif hasattr(stream_obj, "rawdata") and stream_obj.rawdata is not None:
            image_data = base64.b64encode(stream_obj.rawdata).decode("ascii")

        inline_form = il_version_1.PdfInlineForm(
            form_data=image_data, image_parameters=json.dumps(parameters)
        )

        bbox = (0, 0, 1, 1)
        (x, y, w, h) = guarded_bbox(bbox)
        bounds = ((x, y), (x + w, y), (x, y + h), (x + w, y + h))
        final_bbox = get_bound(apply_matrix_pt(ctm, (p, q)) for (p, q) in bounds)

        gs = self.create_graphic_state(
            self.passthrough_per_char_instruction, include_clipping=True, target_ctm=ctm
        )
        pdf_matrix = il_version_1.PdfMatrix(
            a=ctm[0], b=ctm[1], c=ctm[2], d=ctm[3], e=ctm[4], f=ctm[5]
        )
        affine_transform = decompose_ctm(ctm)
        pdf_form_subtype = il_version_1.PdfFormSubtype(pdf_inline_form=inline_form)
        pdf_form = il_version_1.PdfForm(
            box=il_version_1.Box(
                x=final_bbox[0],
                y=final_bbox[1],
                x2=final_bbox[2],
                y2=final_bbox[3],
            ),
            graphic_state=gs,
            pdf_matrix=pdf_matrix,
            pdf_affine_transform=affine_transform,
            pdf_form_subtype=pdf_form_subtype,
            xobj_id=self.xobj_id,
            ctm=list(ctm),
            render_order=self.get_render_order_and_increase(),
            form_type="image",
        )
        self.current_page.pdf_form.append(pdf_form)

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
