from __future__ import annotations

from babeldoc.format.pdf.new_parser.prepared_page import PreparedPdfPage
from babeldoc.format.pdf.new_parser.resources import PageResourceBundle
from babeldoc.format.pdf.new_parser.xobject_content_execution import (
    interpret_operations_with_xobjects,
)
from babeldoc.format.pdf.new_parser.xobject_content_execution import (
    tokenize_content_stream,
)


def interpret_prepared_page(
    page: PreparedPdfPage,
    resource_bundle: PageResourceBundle,
):
    operations = tokenize_content_stream(page.content_bytes)
    events, sidecar = interpret_operations_with_xobjects(
        operations,
        page.resource_tree.xobject_map,
        resource_bundle=resource_bundle,
    )
    return events, resource_bundle, sidecar
