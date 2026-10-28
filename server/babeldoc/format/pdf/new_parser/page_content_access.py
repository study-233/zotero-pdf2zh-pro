from __future__ import annotations

from babeldoc.format.pdf.new_parser.active_value_access import stream_value
from babeldoc.format.pdf.new_parser.prepared_page import PreparedPdfPage


def read_prepared_page_content_streams(page: PreparedPdfPage) -> list[bytes]:
    return list(page.content_streams)


def read_raw_page_content_streams(page: object) -> list[bytes]:
    streams: list[bytes] = []
    for content in page.contents:
        stream = stream_value(content)
        if hasattr(stream, "get_data"):
            streams.append(stream.get_data())
    return streams


def read_page_content_streams(page: object) -> list[bytes]:
    if isinstance(page, PreparedPdfPage):
        return read_prepared_page_content_streams(page)
    return read_raw_page_content_streams(page)


def read_page_content_bytes(page: object) -> bytes:
    return b"\n".join(read_page_content_streams(page))
