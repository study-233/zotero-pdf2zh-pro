from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import Protocol

from babeldoc.format.pdf.new_parser.font_types import PdfFontLike
from babeldoc.format.pdf.new_parser.prepared_page import PreparedFontSpec
from babeldoc.format.pdf.new_parser.prepared_page import PreparedXObject
from babeldoc.format.pdf.new_parser.tokenizer import decode_pdf_name


class FontResolver(Protocol):
    def resolve_font_map(
        self,
        font_specs: tuple[PreparedFontSpec, ...],
    ) -> dict[str, PdfFontLike]: ...


@dataclass
class PageResourceBundle:
    root_font_specs: tuple[PreparedFontSpec, ...]
    root_xobject_map: dict[str, PreparedXObject]
    font_resolver: FontResolver
    direct_font_maps: dict[tuple[str, ...], dict[str, PdfFontLike]] = field(
        default_factory=dict
    )
    font_maps: dict[tuple[str, ...], dict[str, PdfFontLike]] = field(
        default_factory=dict
    )
    fallback_fonts: dict[str, PdfFontLike] = field(default_factory=dict)

    def get_direct_font_map(
        self, xobject_path: tuple[str, ...]
    ) -> dict[str, PdfFontLike]:
        cached = self.direct_font_maps.get(xobject_path)
        if cached is not None:
            return cached

        current_font_specs = self.root_font_specs
        current_xobject_map = self.root_xobject_map
        current_fonts = self.font_resolver.resolve_font_map(current_font_specs)
        self.direct_font_maps.setdefault((), current_fonts)

        traversed_path: tuple[str, ...] = ()
        for name in xobject_path:
            traversed_path = (*traversed_path, name)
            xobject = current_xobject_map.get(name)
            if xobject is None:
                self.direct_font_maps[traversed_path] = {}
                continue

            current_font_specs = xobject.font_specs
            current_xobject_map = xobject.xobject_map
            current_fonts = self.font_resolver.resolve_font_map(current_font_specs)
            self.direct_font_maps[traversed_path] = current_fonts

        return self.direct_font_maps[xobject_path]

    def get_font_map(self, xobject_path: tuple[str, ...]) -> dict[str, PdfFontLike]:
        cached = self.font_maps.get(xobject_path)
        if cached is not None:
            return cached

        font_map: dict[str, PdfFontLike] = {}
        traversed_path: tuple[str, ...] = ()
        font_map.update(_font_name_aliases(self.get_direct_font_map(traversed_path)))
        for name in xobject_path:
            traversed_path = (*traversed_path, name)
            font_map.update(
                _font_name_aliases(self.get_direct_font_map(traversed_path))
            )

        self.font_maps[xobject_path] = font_map

        return font_map

    def get_font(
        self, xobject_path: tuple[str, ...], font_name: str | None
    ) -> PdfFontLike | None:
        if font_name is None:
            return None
        font_map = self.get_font_map(xobject_path)
        decoded_name = decode_pdf_name(font_name)
        font = font_map.get(font_name) or font_map.get(decoded_name)
        if font is not None:
            return font

        # Legacy/pdfminer keeps text extraction alive even when a page references
        # a font name that is absent from the resolved resource tree. Use the
        # same minimal Type1-shaped fallback here so native parsing does not drop
        # whole text runs on malformed pages.
        fallback_name = decoded_name or font_name
        cached = self.fallback_fonts.get(fallback_name)
        if cached is not None:
            return cached

        fallback_map = self.font_resolver.resolve_font_map(
            (
                PreparedFontSpec(
                    name=fallback_name,
                    objid=None,
                    spec={"Subtype": "Type1", "BaseFont": fallback_name},
                ),
            )
        )
        font = fallback_map.get(fallback_name)
        if font is not None:
            font.font_id_temp = "UNKNOW"
            self.fallback_fonts[fallback_name] = font
        return font


def _font_name_aliases(font_map: dict[str, PdfFontLike]) -> dict[str, PdfFontLike]:
    result = dict(font_map)
    for name, font in font_map.items():
        for alias in _decoded_pdf_name_aliases(name):
            result.setdefault(alias, font)
    return result


def _decoded_pdf_name_aliases(name: str) -> tuple[str, ...]:
    aliases: list[str] = []
    current = name
    while True:
        decoded = decode_pdf_name(current)
        if decoded == current:
            break
        aliases.append(decoded)
        current = decoded
    return tuple(aliases)
