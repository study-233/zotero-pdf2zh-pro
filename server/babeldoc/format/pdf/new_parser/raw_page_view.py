from __future__ import annotations

from dataclasses import dataclass

from babeldoc.format.pdf.new_parser.page_content_access import (
    read_raw_page_content_streams,
)


@dataclass(frozen=True, slots=True)
class RawPageView:
    pageno: int
    cropbox: tuple[float, float, float, float]
    rotate: int
    resources: dict[object, object] | None
    content_streams: tuple[bytes, ...]


def build_raw_page_view(raw_page: object, *, pageno: int) -> RawPageView:
    raw_page.pageno = pageno
    return RawPageView(
        pageno=pageno,
        cropbox=tuple(float(value) for value in raw_page.cropbox[:4]),
        rotate=int(getattr(raw_page, "rotate", 0)),
        resources=getattr(raw_page, "resources", None),
        content_streams=tuple(read_raw_page_content_streams(raw_page)),
    )
