from __future__ import annotations

from babeldoc.format.pdf.new_parser.prepared_page import PreparedPageResources
from babeldoc.format.pdf.new_parser.prepared_page import PreparedPdfPage
from babeldoc.format.pdf.new_parser.prepared_resource_builder import (
    DEFAULT_OBJECT_ACCESS,
)
from babeldoc.format.pdf.new_parser.prepared_resource_builder import (
    PreparedObjectAccess,
)
from babeldoc.format.pdf.new_parser.prepared_resource_builder import (
    build_prepared_font_specs,
)
from babeldoc.format.pdf.new_parser.prepared_resource_builder import (
    build_prepared_xobject_map,
)
from babeldoc.format.pdf.new_parser.raw_page_view import RawPageView


def build_prepared_pdf_page(
    page_view: RawPageView,
    *,
    object_access: PreparedObjectAccess = DEFAULT_OBJECT_ACCESS,
) -> PreparedPdfPage:
    resources = page_view.resources
    return PreparedPdfPage(
        pageno=page_view.pageno,
        cropbox=page_view.cropbox,
        rotate=page_view.rotate,
        resource_tree=PreparedPageResources(
            root_font_specs=build_prepared_font_specs(
                resources,
                object_access=object_access,
            ),
            xobject_map=build_prepared_xobject_map(
                resources,
                object_access=object_access,
            ),
        ),
        content_streams=page_view.content_streams,
        content_bytes=b"\n".join(page_view.content_streams),
    )
