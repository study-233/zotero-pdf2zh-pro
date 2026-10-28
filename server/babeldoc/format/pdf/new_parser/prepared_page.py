from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class PreparedFontSpec:
    name: str
    objid: int | None
    spec: dict[object, object]
    resolve_indirect: Callable[[object], object] | None = None


@dataclass(frozen=True)
class PreparedXObject:
    subtype_name: str | None
    xref_id: int | None
    font_specs: tuple[PreparedFontSpec, ...]
    xobject_map: dict[str, PreparedXObject]
    bbox: tuple[float, float, float, float]
    matrix: tuple[float, float, float, float, float, float]
    data: bytes

    @property
    def is_form(self) -> bool:
        return self.subtype_name == "Form"

    @property
    def is_image(self) -> bool:
        return self.subtype_name == "Image"


@dataclass(frozen=True, slots=True)
class PreparedPageResources:
    root_font_specs: tuple[PreparedFontSpec, ...]
    xobject_map: dict[str, PreparedXObject]


@dataclass(slots=True)
class PreparedPdfPage:
    pageno: int
    cropbox: tuple[float, float, float, float]
    rotate: int
    resource_tree: PreparedPageResources
    content_streams: tuple[bytes, ...]
    content_bytes: bytes


def legacy_page_cropbox(page: PreparedPdfPage) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = page.cropbox
    if page.rotate in (90, 270):
        return float(y0), float(x1), float(y1), float(x0)
    return float(x0), float(y0), float(x1), float(y1)


def raw_page_cropbox(page: PreparedPdfPage) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = page.cropbox
    return float(x0), float(y0), float(x1), float(y1)


def il_page_cropbox(
    page: PreparedPdfPage,
    *,
    visual_roundtrip_mode: bool = False,
) -> tuple[float, float, float, float]:
    if visual_roundtrip_mode:
        return raw_page_cropbox(page)
    return legacy_page_cropbox(page)


def page_base_operation_cropbox(
    page: PreparedPdfPage,
    *,
    visual_roundtrip_mode: bool = False,
) -> tuple[float, float, float, float]:
    if visual_roundtrip_mode:
        return raw_page_cropbox(page)
    return legacy_page_cropbox(page)
