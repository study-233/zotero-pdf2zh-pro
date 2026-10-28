from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from babeldoc.format.pdf.new_parser.pymupdf_prepared_page_access import (
    load_prepared_pdf_pages,
)
from babeldoc.format.pdf.parse_shared import build_parse_only_config
from babeldoc.format.pdf.parse_shared import prepare_pdf_for_parse


@contextmanager
def prepared_pdf_pages(
    pdf_path: str | Path,
    *,
    pages: str | None = None,
):
    pdf_path = Path(pdf_path)
    with TemporaryDirectory(prefix="babeldoc-native-page-api-") as tmpdir:
        config = build_parse_only_config(
            pdf_path,
            pages=pages,
            working_dir=tmpdir,
            debug=False,
        )
        doc_pdf = None
        try:
            doc_pdf, temp_pdf_path = prepare_pdf_for_parse(pdf_path, config)
            with load_prepared_pdf_pages(temp_pdf_path) as prepared_pages:
                yield prepared_pages
        finally:
            if doc_pdf is not None:
                doc_pdf.close()
            config.cleanup_temp_files()
