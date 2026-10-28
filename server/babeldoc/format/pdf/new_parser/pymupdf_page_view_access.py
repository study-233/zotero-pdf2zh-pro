from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

from babeldoc.format.pdf.new_parser.object_model import PdfIndirectRef
from babeldoc.format.pdf.new_parser.object_model import PdfObjectDict
from babeldoc.format.pdf.new_parser.pymupdf_object_access import build_object_store
from babeldoc.format.pdf.new_parser.pymupdf_object_access import parse_page_contents
from babeldoc.format.pdf.new_parser.pymupdf_object_access import parse_page_resources
from babeldoc.format.pdf.new_parser.raw_page_view import RawPageView


def _read_content_streams(contents: object, object_store) -> tuple[bytes, ...]:
    resolved_access = object_store.as_resolved_access()

    def read_many(value: object) -> tuple[bytes, ...]:
        resolved = resolved_access.resolve(value)
        if isinstance(resolved, list | tuple):
            result: list[bytes] = []
            for item in resolved:
                result.extend(read_many(item))
            return tuple(result)
        stream = resolved_access.stream_value(resolved)
        return (stream.get_data(),)

    if isinstance(contents, list | tuple | PdfIndirectRef):
        return read_many(contents)
    return ()


def _read_page_cropbox(
    page_xref: int, page, object_store
) -> tuple[float, float, float, float]:
    page_obj = object_store.resolve_xref(page_xref)
    if isinstance(page_obj, PdfObjectDict):
        box = page_obj.get("CropBox") or page_obj.get("MediaBox")
        if isinstance(box, list) and len(box) >= 4:
            return tuple(float(value) for value in box[:4])
    return tuple(float(value) for value in page.cropbox)


@contextmanager
def load_page_views(
    pdf_path: str | Path,
    *,
    should_include_page: Callable[[int], bool] | None = None,
):
    import fitz

    document = fitz.open(pdf_path)
    try:
        object_store = build_object_store(document)
        page_views: list[RawPageView] = []
        for pageno, page in enumerate(document):
            page_number = pageno + 1
            if should_include_page is not None and not should_include_page(page_number):
                continue
            resources = parse_page_resources(document, page.xref)
            contents = parse_page_contents(document, page.xref)
            page_views.append(
                RawPageView(
                    pageno=pageno,
                    cropbox=_read_page_cropbox(page.xref, page, object_store),
                    rotate=int(page.rotation),
                    resources=resources,
                    content_streams=_read_content_streams(contents, object_store),
                )
            )
        yield page_views
    finally:
        document.close()
