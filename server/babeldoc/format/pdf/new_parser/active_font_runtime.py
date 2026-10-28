from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import Protocol

from babeldoc.format.pdf.new_parser.active_object_projection import project_font_spec
from babeldoc.format.pdf.new_parser.font_types import PdfRuntimeFontLike
from babeldoc.format.pdf.new_parser.prepared_page import PreparedFontSpec


@dataclass(slots=True)
class ActiveFontAdapter:
    backend: PdfRuntimeFontLike
    xobj_id: int | None = None
    legacy_descent: float | None = None
    font_id_temp: str | None = None

    @property
    def descent(self) -> float:
        return self.backend.descent

    @descent.setter
    def descent(self, value: float) -> None:
        self.backend.descent = value

    @property
    def fontname(self) -> str | bytes:
        return self.backend.fontname

    def decode(self, data: bytes) -> object:
        return self.backend.decode(data)

    def unicode_text(self, cid: int, fallback_text: str) -> str:
        return self.backend.unicode_text(cid, fallback_text)

    def is_multibyte(self) -> bool:
        return self.backend.is_multibyte()

    def is_vertical(self) -> bool:
        return self.backend.is_vertical()

    def char_width(self, cid: int) -> float:
        return self.backend.char_width(cid)

    def char_disp(self, cid: int) -> float | tuple[float | None, float]:
        return self.backend.char_disp(cid)

    def get_descent(self) -> float:
        return self.backend.get_descent()

    def runtime_identity(self) -> int:
        return self.backend.runtime_identity()

    def compute_encoding_length(self, *, mupdf: object, xref_id: int) -> int:
        return self.backend.compute_encoding_length(mupdf=mupdf, xref_id=xref_id)

    def __getattr__(self, name: str):
        return getattr(self.backend, name)


class RuntimeFontFactory(Protocol):
    def create_font(
        self,
        objid: int | None,
        runtime_spec: dict[object, object],
    ) -> PdfRuntimeFontLike: ...


def resolve_active_font_map(
    font_specs: tuple[PreparedFontSpec, ...],
    font_factory: RuntimeFontFactory,
    legacy_descents: dict[object, float],
    runtime_cache: dict[object, object],
):
    if not font_specs:
        return {}
    result: dict[str, ActiveFontAdapter] = {}
    for font_spec in font_specs:
        cache_key = font_spec.objid
        backend = runtime_cache.get(cache_key) if cache_key is not None else None
        if backend is None:
            runtime_spec = project_font_spec(
                font_spec.spec,
                resolve_indirect=font_spec.resolve_indirect,
            )
            backend = font_factory.create_font(font_spec.objid, runtime_spec)
            if cache_key is not None:
                runtime_cache[cache_key] = backend
        descent_root = font_spec.objid if font_spec.objid is not None else id(backend)
        descent_key = (descent_root, font_spec.name)
        if descent_key not in legacy_descents:
            legacy_descents[descent_key] = backend.descent
        backend.descent = 0
        font = ActiveFontAdapter(
            backend=backend,
            xobj_id=font_spec.objid,
            legacy_descent=legacy_descents[descent_key],
        )
        result[font_spec.name] = font
    return result


@dataclass(slots=True)
class ActiveFontResolver:
    font_factory: RuntimeFontFactory
    legacy_descents: dict[object, float] = field(default_factory=dict)
    runtime_cache: dict[object, object] = field(default_factory=dict)

    def resolve_font_map(self, font_specs: tuple[PreparedFontSpec, ...]):
        return resolve_active_font_map(
            font_specs,
            self.font_factory,
            self.legacy_descents,
            self.runtime_cache,
        )
