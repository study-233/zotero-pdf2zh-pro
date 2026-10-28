from __future__ import annotations

from dataclasses import dataclass

from babeldoc.format.pdf.new_parser.active_direct_font_backend import (
    construct_active_direct_runtime_font,
)
from babeldoc.format.pdf.new_parser.font_types import PdfRuntimeFontLike


@dataclass(slots=True)
class ActiveFontFactory:
    def create_font(
        self, objid: int | None, runtime_spec: dict[object, object]
    ) -> PdfRuntimeFontLike:
        _ = objid
        direct_font = construct_active_direct_runtime_font(runtime_spec)
        if direct_font is not None:
            return direct_font
        msg = (
            f"Unsupported active runtime font subtype: {runtime_spec.get('Subtype')!r}"
        )
        raise NotImplementedError(msg)
