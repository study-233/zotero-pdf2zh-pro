from __future__ import annotations

from typing import Protocol

from babeldoc.format.pdf.new_parser.prepared_page import PreparedPdfPage


class PageInterpreter(Protocol):
    def begin_page(self, page: PreparedPdfPage, pageno: int) -> None: ...

    def process_page(self, page: PreparedPdfPage) -> object: ...

    def end_page(self, page: PreparedPdfPage, pageno: int) -> None: ...
