from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

from babeldoc.format.pdf.new_parser.prepared_page import PreparedPdfPage
from babeldoc.format.pdf.new_parser.prepared_page_access import build_prepared_pdf_page
from babeldoc.format.pdf.new_parser.pymupdf_object_access import build_object_store
from babeldoc.format.pdf.new_parser.pymupdf_page_view_access import load_page_views


@contextmanager
def load_prepared_pdf_pages(
    temp_pdf_path: str | Path,
    *,
    should_include_page: Callable[[int], bool] | None = None,
):
    with load_page_views(
        temp_pdf_path,
        should_include_page=should_include_page,
    ) as raw_pages:
        import fitz

        document = fitz.open(temp_pdf_path)
        try:
            object_store = build_object_store(document)
            object_access = (
                object_store.as_resolved_access().as_prepared_object_access()
            )
            pages: list[PreparedPdfPage] = []
            for page_view in raw_pages:
                pages.append(
                    build_prepared_pdf_page(page_view, object_access=object_access)
                )
            yield pages
        finally:
            document.close()
