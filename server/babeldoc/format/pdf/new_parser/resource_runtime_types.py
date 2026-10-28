from __future__ import annotations

from typing import Protocol

from babeldoc.format.pdf.new_parser.prepared_page import PreparedPageResources
from babeldoc.format.pdf.new_parser.resources import PageResourceBundle


class PageResourceRuntime(Protocol):
    def build_page_resource_bundle(
        self,
        resource_tree: PreparedPageResources,
    ) -> PageResourceBundle: ...
