from __future__ import annotations

import logging

from babeldoc.format.pdf.new_parser.base_operations import BaseOperationSidecar
from babeldoc.format.pdf.new_parser.base_operations import (
    collect_page_base_inner_operation,
)
from babeldoc.format.pdf.new_parser.base_operations import compute_xobject_end_operation
from babeldoc.format.pdf.new_parser.interpreter import BeginXObjectEvent
from babeldoc.format.pdf.new_parser.interpreter import CollectingEventSink
from babeldoc.format.pdf.new_parser.interpreter import EndXObjectEvent
from babeldoc.format.pdf.new_parser.interpreter import ImageXObjectEvent
from babeldoc.format.pdf.new_parser.interpreter import TextContentInterpreter
from babeldoc.format.pdf.new_parser.interpreter import interpret_operations
from babeldoc.format.pdf.new_parser.prepared_page import PreparedXObject
from babeldoc.format.pdf.new_parser.resources import PageResourceBundle
from babeldoc.format.pdf.new_parser.state import InterpreterState
from babeldoc.format.pdf.new_parser.state import multiply_matrices
from babeldoc.format.pdf.new_parser.tokenizer import PdfOperation
from babeldoc.format.pdf.new_parser.tokenizer import tokenize_operations

logger = logging.getLogger(__name__)

MAX_XOBJECT_NESTING_DEPTH = 64


def tokenize_content_stream(content: bytes) -> list[PdfOperation]:
    return tokenize_operations(content)


def interpret_content_stream(content: bytes) -> list[object]:
    operations = tokenize_content_stream(content)
    return interpret_operations(operations)


def interpret_operations_with_xobjects(
    operations: list[PdfOperation],
    xobject_map: dict[str, PreparedXObject],
    *,
    resource_bundle: PageResourceBundle,
    initial_state: InterpreterState | None = None,
    xobject_path: tuple[str, ...] = (),
    active_xobject_ids: frozenset[int] | None = None,
    max_xobject_depth: int = MAX_XOBJECT_NESTING_DEPTH,
) -> tuple[list[object], BaseOperationSidecar]:
    if active_xobject_ids is None:
        active_xobject_ids = frozenset()
    sink = CollectingEventSink()
    interpreter = TextContentInterpreter(sink=sink)
    if initial_state is not None:
        interpreter.state = initial_state
    interpreter.xobject_path = xobject_path
    interpreter.font_resolver = resource_bundle.get_font
    sidecar = BaseOperationSidecar(
        page_inner_operation=collect_page_base_inner_operation(operations),
        xobject_end_operations={},
    )

    def handle_xobject(name: str, state: InterpreterState) -> list[object]:
        details = xobject_map.get(name)
        if details is None:
            return []
        subtype_name = details.subtype_name
        if subtype_name == "Image":
            return [
                ImageXObjectEvent(
                    name=name,
                    bbox=(0.0, 0.0, 1.0, 1.0),
                    matrix=(1.0, 0.0, 0.0, 1.0, 0.0, 0.0),
                    ctm=state.graphics_state.ctm,
                    xref_id=details.xref_id,
                    xobject_path=xobject_path,
                    text_clip_active=state.graphics_state.text_clip_active,
                    text_clip_passthrough_operation=(
                        state.graphics_state.text_clip_passthrough_operation
                    ),
                    text_clip_passthrough_ctm=(
                        state.graphics_state.text_clip_passthrough_ctm
                    ),
                ),
            ]
        if not details.is_form:
            return []
        child_path = (*xobject_path, name)
        details_identity = id(details)
        if details_identity in active_xobject_ids:
            logger.warning(
                "Skipping recursive Form XObject %s at path %s",
                name,
                "/".join(child_path),
            )
            return []
        if len(child_path) > max_xobject_depth:
            logger.warning(
                "Skipping Form XObject %s at depth %s over limit %s",
                name,
                len(child_path),
                max_xobject_depth,
            )
            return []
        child_state = InterpreterState()
        child_state.graphics_state.ctm = multiply_matrices(
            details.matrix,
            state.graphics_state.ctm,
        )
        child_operations = tokenize_content_stream(details.data)
        child_events, child_sidecar = interpret_operations_with_xobjects(
            child_operations,
            details.xobject_map,
            resource_bundle=resource_bundle,
            initial_state=child_state,
            xobject_path=child_path,
            active_xobject_ids=active_xobject_ids | {details_identity},
            max_xobject_depth=max_xobject_depth,
        )
        sidecar.xobject_end_operations[child_path] = compute_xobject_end_operation(
            details.matrix,
            state.graphics_state.ctm,
        )
        sidecar.xobject_end_operations.update(child_sidecar.xobject_end_operations)
        return [
            BeginXObjectEvent(
                name=name,
                subtype=subtype_name,
                bbox=details.bbox,
                matrix=details.matrix,
                ctm=state.graphics_state.ctm,
                xref_id=details.xref_id,
                xobject_path=xobject_path,
            ),
            *child_events,
            EndXObjectEvent(name=name, subtype=subtype_name),
        ]

    interpreter.xobject_handler = handle_xobject
    return interpreter.run(operations), sidecar
