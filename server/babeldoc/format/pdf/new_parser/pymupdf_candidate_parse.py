from __future__ import annotations

from pathlib import Path

from babeldoc.format.pdf.new_parser.active_font_resource_runtime import (
    create_active_font_resource_runtime,
)
from babeldoc.format.pdf.new_parser.active_parse_runtime import run_active_parse_session
from babeldoc.format.pdf.new_parser.pymupdf_page_execution_session import (
    PyMuPdfPageExecutionSession,
)


def parse_with_pymupdf_prepared_to_legacy_ir(
    pdf_path: str | Path,
    *,
    pages: str | None = None,
    working_dir: str | Path | None = None,
    debug: bool = False,
):
    from babeldoc.format.pdf.new_parser.text_positioning import NativeTextRunPositioner

    return run_active_parse_session(
        pdf_path,
        pages=pages,
        working_dir=working_dir,
        debug=debug,
        create_session=lambda config, sink: PyMuPdfPageExecutionSession(
            config=config,
            sink=sink,
            create_text_run_positioner=lambda _resource_runtime: (
                NativeTextRunPositioner()
            ),
            create_resource_runtime=create_active_font_resource_runtime,
        ),
    )
