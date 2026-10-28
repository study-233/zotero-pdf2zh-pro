from __future__ import annotations

from pathlib import Path

import pymupdf

from babeldoc.format.pdf.new_parser.active_font_resource_runtime import (
    create_active_font_resource_runtime,
)
from babeldoc.format.pdf.new_parser.active_parse_runtime import run_active_parse_session
from babeldoc.format.pdf.new_parser.native_page_execution_session import (
    NativePageExecutionSession,
)
from babeldoc.format.pdf.new_parser.native_page_interpreter import (
    create_native_page_interpreter,
)
from babeldoc.format.pdf.new_parser.prepared_page_execution import run_prepared_pages
from babeldoc.format.pdf.new_parser.pymupdf_prepared_page_access import (
    load_prepared_pdf_pages,
)
from babeldoc.format.pdf.translation_config import TranslationConfig


def parse_with_new_parser_to_legacy_ir(
    pdf_path: str | Path,
    *,
    pages: str | None = None,
    working_dir: str | Path | None = None,
    debug: bool = False,
):
    return parse_with_native_builtin_positioner_to_legacy_ir(
        pdf_path,
        pages=pages,
        working_dir=working_dir,
        debug=debug,
    )


def parse_prepared_pdf_with_new_parser_to_legacy_ir(
    temp_pdf_path: str | Path,
    *,
    config: TranslationConfig,
    doc_pdf: pymupdf.Document,
):
    """Run the active parser inside the product pipeline.

    Unlike the parse-only helpers above, this reuses the caller's
    TranslationConfig, progress monitor, prepared temp PDF, and open PyMuPDF
    document. That keeps the product parse stage aligned with the surrounding
    high-level translation flow.
    """
    from babeldoc.format.pdf.document_il.frontend.il_creater_active import (
        ActiveILCreater,
    )
    from babeldoc.format.pdf.new_parser.text_positioning import NativeTextRunPositioner

    sink = ActiveILCreater(config)
    sink.mupdf = doc_pdf
    resource_runtime = create_active_font_resource_runtime()
    with load_prepared_pdf_pages(
        temp_pdf_path,
        should_include_page=config.should_translate_page,
    ) as prepared_pages:
        page_interpreter = create_native_page_interpreter(
            sink,
            NativeTextRunPositioner(),
            resource_runtime,
            config=config,
        )
        return run_prepared_pages(
            sink,
            config.should_translate_page,
            prepared_pages,
            page_interpreter,
        )


def parse_with_native_text_only_to_legacy_ir(
    pdf_path: str | Path,
    *,
    pages: str | None = None,
    working_dir: str | Path | None = None,
    debug: bool = False,
):
    from babeldoc.format.pdf.new_parser.text_positioning import NativeTextRunPositioner

    return _parse_with_native_positioner_to_legacy_ir(
        pdf_path,
        pages=pages,
        working_dir=working_dir,
        debug=debug,
        text_run_positioner=NativeTextRunPositioner(),
        create_resource_runtime=create_active_font_resource_runtime,
    )


def parse_with_native_builtin_positioner_to_legacy_ir(
    pdf_path: str | Path,
    *,
    pages: str | None = None,
    working_dir: str | Path | None = None,
    debug: bool = False,
):
    from babeldoc.format.pdf.new_parser.text_positioning import NativeTextRunPositioner

    return _parse_with_native_positioner_to_legacy_ir(
        pdf_path,
        pages=pages,
        working_dir=working_dir,
        debug=debug,
        text_run_positioner=NativeTextRunPositioner(),
        create_resource_runtime=create_active_font_resource_runtime,
    )


def _parse_with_native_positioner_to_legacy_ir(
    pdf_path: str | Path,
    *,
    pages: str | None = None,
    working_dir: str | Path | None = None,
    debug: bool = False,
    text_run_positioner=None,
    text_run_positioner_factory=None,
    create_resource_runtime=None,
):
    return _parse_with_positioner_to_legacy_ir(
        pdf_path,
        pages=pages,
        working_dir=working_dir,
        debug=debug,
        create_text_run_positioner=(
            text_run_positioner_factory
            if text_run_positioner_factory is not None
            else (lambda _resource_runtime: text_run_positioner)
        ),
        create_resource_runtime=create_resource_runtime,
        session_type=NativePageExecutionSession,
    )


def _parse_with_positioner_to_legacy_ir(
    pdf_path: str | Path,
    *,
    pages: str | None = None,
    working_dir: str | Path | None = None,
    debug: bool = False,
    create_text_run_positioner,
    create_resource_runtime=None,
    session_type,
):
    return run_active_parse_session(
        pdf_path,
        pages=pages,
        working_dir=working_dir,
        debug=debug,
        create_session=lambda config, sink: session_type(
            config=config,
            sink=sink,
            create_text_run_positioner=create_text_run_positioner,
            **(
                {"create_resource_runtime": create_resource_runtime}
                if create_resource_runtime is not None
                else {}
            ),
        ),
    )
