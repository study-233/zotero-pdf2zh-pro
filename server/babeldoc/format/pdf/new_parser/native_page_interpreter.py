from __future__ import annotations

from babeldoc.format.pdf.new_parser.base_operations import wrap_page_base_operation
from babeldoc.format.pdf.new_parser.bridge_types import EmitterSink
from babeldoc.format.pdf.new_parser.page_api import interpret_page_with_resource_bundle
from babeldoc.format.pdf.new_parser.page_interpreter import PageInterpreter
from babeldoc.format.pdf.new_parser.prepared_page import PreparedPdfPage
from babeldoc.format.pdf.new_parser.prepared_page import il_page_cropbox
from babeldoc.format.pdf.new_parser.prepared_page import page_base_operation_cropbox
from babeldoc.format.pdf.new_parser.resource_runtime_types import PageResourceRuntime
from babeldoc.format.pdf.new_parser.sinks.native_text import (
    emit_native_text_events_to_legacy_sink,
)


def create_native_page_interpreter(
    sink: EmitterSink,
    text_run_positioner: object,
    resource_runtime: PageResourceRuntime,
    config: object | None = None,
) -> PageInterpreter:
    _ = config

    class _NativePageInterpreter:
        def begin_page(self, page: PreparedPdfPage, pageno: int) -> None:
            sink.on_page_start()
            x0, y0, x1, y1 = il_page_cropbox(page)
            sink.on_page_crop_box(float(x0), float(y0), float(x1), float(y1))
            raw_x0, raw_y0, raw_x1, raw_y1 = page.cropbox
            sink.on_page_media_box(
                float(raw_x0),
                float(raw_y0),
                float(raw_x1),
                float(raw_y1),
            )
            sink.on_page_number(pageno)

        def process_page(self, page: PreparedPdfPage) -> object:
            resource_bundle = resource_runtime.build_page_resource_bundle(
                page.resource_tree,
            )
            events, resource_bundle, base_operations = (
                interpret_page_with_resource_bundle(
                    page,
                    resource_bundle,
                )
            )
            emit_native_text_events_to_legacy_sink(
                events,
                resource_bundle,
                sink,
                xobject_end_operations=base_operations.xobject_end_operations,
                text_run_positioner=text_run_positioner,
            )
            x0, y0, x1, y1 = page_base_operation_cropbox(page)
            return wrap_page_base_operation(
                base_operations.page_inner_operation,
                (x0, y0, x1, y1),
            )

        def end_page(self, page: PreparedPdfPage, pageno: int) -> None:
            _ = page
            _ = pageno
            # PageExecutionSession owns the single sink.on_page_end() call.
            # Keeping this hook no-op avoids double-counting product valid-text
            # stats while preserving the PageInterpreter interface.

    return _NativePageInterpreter()
