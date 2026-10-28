from __future__ import annotations

from babeldoc.format.pdf.new_parser.page_content_access import (
    read_page_content_bytes as _read_page_content_bytes,
)
from babeldoc.format.pdf.new_parser.page_content_access import (
    read_page_content_streams as _read_page_content_streams,
)
from babeldoc.format.pdf.new_parser.page_content_execution import (
    interpret_prepared_page,
)
from babeldoc.format.pdf.new_parser.page_content_execution import (
    tokenize_content_stream as _tokenize_content_stream,
)
from babeldoc.format.pdf.new_parser.prepared_page import PreparedPdfPage
from babeldoc.format.pdf.new_parser.prepared_page_debug_access import (
    prepared_pdf_pages as _prepared_pdf_pages,
)
from babeldoc.format.pdf.new_parser.resources import PageResourceBundle

prepared_pdf_pages = _prepared_pdf_pages
read_page_content_bytes = _read_page_content_bytes
read_page_content_streams = _read_page_content_streams
tokenize_content_stream = _tokenize_content_stream


def interpret_page_with_resource_bundle(
    page: PreparedPdfPage,
    resource_bundle: PageResourceBundle,
):
    return interpret_prepared_page(page, resource_bundle)
