from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field

from babeldoc.format.pdf.new_parser.active_font_backend import ActiveFontFactory
from babeldoc.format.pdf.new_parser.active_font_runtime import ActiveFontResolver
from babeldoc.format.pdf.new_parser.prepared_page import PreparedPageResources
from babeldoc.format.pdf.new_parser.resources import PageResourceBundle


@dataclass(slots=True)
class ActiveFontResourceRuntime:
    _font_factory: object
    _runtime_cache: dict[object, object] = field(default_factory=dict)

    def build_page_resource_bundle(
        self,
        resource_tree: PreparedPageResources,
    ) -> PageResourceBundle:
        bundle = PageResourceBundle(
            root_font_specs=resource_tree.root_font_specs,
            root_xobject_map=resource_tree.xobject_map,
            # Keep the runtime font cache across pages so direct-constructor fonts
            # preserve the same evolving backend state as the compatibility
            # path's shared font cache. `legacy_descents` still stays page-local
            # inside each fresh resolver so each page snapshots the font's
            # current descent.
            font_resolver=ActiveFontResolver(
                font_factory=self._font_factory,
                runtime_cache=self._runtime_cache,
            ),
        )
        bundle.get_direct_font_map(())
        return bundle


def create_active_font_resource_runtime(
    *,
    font_factory: object | None = None,
) -> ActiveFontResourceRuntime:
    factory = font_factory if font_factory is not None else ActiveFontFactory()
    return ActiveFontResourceRuntime(_font_factory=factory)
