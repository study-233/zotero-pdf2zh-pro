from __future__ import annotations

from pathlib import Path

from babeldoc.format.pdf.document_il.frontend.il_creater import ILCreater
from babeldoc.format.pdf.legacy_parse import start_parse_il
from babeldoc.format.pdf.new_parser.sinks.legacy_ir import LegacyIRSink
from babeldoc.format.pdf.parse_shared import build_parse_only_config
from babeldoc.format.pdf.parse_shared import prepare_pdf_for_parse


def parse_with_legacy_ir(
    pdf_path: str | Path,
    *,
    pages: str | None = None,
    working_dir: str | Path | None = None,
    debug: bool = False,
):
    pdf_path = Path(pdf_path)
    config = build_parse_only_config(
        pdf_path,
        pages=pages,
        working_dir=working_dir,
        debug=debug,
    )
    doc_pdf = None
    try:
        doc_pdf, temp_pdf_path = prepare_pdf_for_parse(pdf_path, config)
        il_creater = ILCreater(config)
        sink = LegacyIRSink(il_creater)
        sink.mupdf = doc_pdf
        with temp_pdf_path.open("rb") as handle:
            start_parse_il(
                handle,
                doc_zh=doc_pdf,
                resfont=None,
                il_creater=il_creater,
                translation_config=config,
            )
        return sink.create_document()
    finally:
        if doc_pdf is not None:
            doc_pdf.close()
        config.cleanup_temp_files()


def parse_with_new_parser_to_legacy_ir(
    pdf_path: str | Path,
    *,
    pages: str | None = None,
    working_dir: str | Path | None = None,
    debug: bool = False,
):
    from babeldoc.format.pdf.new_parser.native_parse import (
        parse_with_new_parser_to_legacy_ir as _active_parse_with_new_parser,
    )

    return _active_parse_with_new_parser(
        pdf_path,
        pages=pages,
        working_dir=working_dir,
        debug=debug,
    )


def parse_with_native_text_only_to_legacy_ir(
    pdf_path: str | Path,
    *,
    pages: str | None = None,
    working_dir: str | Path | None = None,
    debug: bool = False,
):
    from babeldoc.format.pdf.new_parser.native_parse import (
        parse_with_native_text_only_to_legacy_ir as _active_parse_native_text_only,
    )

    return _active_parse_native_text_only(
        pdf_path,
        pages=pages,
        working_dir=working_dir,
        debug=debug,
    )


def parse_with_native_builtin_positioner_to_legacy_ir(
    pdf_path: str | Path,
    *,
    pages: str | None = None,
    working_dir: str | Path | None = None,
    debug: bool = False,
):
    from babeldoc.format.pdf.new_parser.native_parse import (
        parse_with_native_builtin_positioner_to_legacy_ir as _active_parse_native_builtin,
    )

    return _active_parse_native_builtin(
        pdf_path,
        pages=pages,
        working_dir=working_dir,
        debug=debug,
    )


def parse_with_pymupdf_prepared_to_legacy_ir(
    pdf_path: str | Path,
    *,
    pages: str | None = None,
    working_dir: str | Path | None = None,
    debug: bool = False,
):
    from babeldoc.format.pdf.new_parser.pymupdf_candidate_parse import (
        parse_with_pymupdf_prepared_to_legacy_ir as _active_parse_pymupdf_prepared,
    )

    return _active_parse_pymupdf_prepared(
        pdf_path,
        pages=pages,
        working_dir=working_dir,
        debug=debug,
    )
