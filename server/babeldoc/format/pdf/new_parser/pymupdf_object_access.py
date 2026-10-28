from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from babeldoc.format.pdf.new_parser.object_model import PdfIndirectRef
from babeldoc.format.pdf.new_parser.object_model import PdfObjectDict
from babeldoc.format.pdf.new_parser.object_model import PdfObjectStream
from babeldoc.format.pdf.new_parser.object_parser import parse_object_bytes
from babeldoc.format.pdf.new_parser.resolved_object_access import ResolvedObjectAccess
from babeldoc.format.pdf.new_parser.resolved_object_access import object_dict

if TYPE_CHECKING:
    pass


def parse_object_text(text: str, *, objid: int | None = None) -> object:
    parsed = parse_object_bytes(text.encode("latin-1"))
    if isinstance(parsed, dict):
        return object_dict(parsed, objid=objid)
    return parsed


@dataclass(slots=True)
class PyMuPdfObjectStore:
    document: object
    cache: dict[int, object]

    def resolve_xref(self, xref: int) -> object:
        cached = self.cache.get(xref)
        if cached is not None:
            return cached

        parsed = _resolve_xref_object(self.document, xref)
        if isinstance(parsed, PdfObjectDict):
            if _is_image_xobject_dict(parsed):
                self.cache[xref] = parsed
                return parsed
            stream_data = _read_stream_data(self.document, xref)
            if stream_data is not None:
                parsed_stream = PdfObjectStream(
                    attrs=parsed,
                    rawdata=stream_data,
                    objid=xref,
                    decoded=True,
                )
                self.cache[xref] = parsed_stream
                return parsed_stream

        self.cache[xref] = parsed
        return parsed

    def as_resolved_access(self) -> ResolvedObjectAccess:
        return ResolvedObjectAccess(self.cache, resolver=self.resolve_xref)


def build_object_store(document: object) -> PyMuPdfObjectStore:
    return PyMuPdfObjectStore(document=document, cache={})


def _is_image_xobject_dict(parsed: PdfObjectDict) -> bool:
    return parsed.get("Subtype") == "Image"


def _find_inherited_page_key(
    document: object, page_xref: int, key: str
) -> tuple[str, str]:
    current_xref = page_xref
    visited: set[int] = set()
    while current_xref not in visited:
        visited.add(current_xref)
        keys = document.xref_get_keys(current_xref)
        if key in keys:
            kind, value = document.xref_get_key(current_xref, key)
            if kind != "null":
                return kind, value
        if "Parent" not in keys:
            break
        parent_kind, parent_value = document.xref_get_key(current_xref, "Parent")
        if parent_kind != "xref":
            break
        current_xref = int(parent_value.split()[0])
    return "null", "null"


def parse_page_resources(document: object, page_xref: int) -> object:
    kind, value = _find_inherited_page_key(document, page_xref, "Resources")
    if kind == "xref":
        return PdfIndirectRef(int(value.split()[0]), 0)
    if kind == "dict":
        return parse_object_text(value)
    if kind == "null":
        return object_dict({})
    raise ValueError(f"Unsupported Resources kind: {kind!r}")


def parse_page_contents(document: object, page_xref: int) -> object:
    kind, value = document.xref_get_key(page_xref, "Contents")
    if kind == "xref":
        return PdfIndirectRef(int(value.split()[0]), 0)
    if kind == "array":
        return parse_object_text(value)
    if kind == "null":
        return ()
    raise ValueError(f"Unsupported Contents kind: {kind!r}")


def _read_stream_data(document: object, xref: int) -> bytes | None:
    try:
        stream = document.xref_stream(xref)
    except Exception:
        return None
    return stream


def _resolve_xref_object(document: object, xref: int) -> object:
    try:
        object_text = document.xref_object(xref, compressed=False)
    except Exception:
        fallback = _reconstruct_xref_object(document, xref)
        if fallback is not None:
            return fallback
        raise
    return parse_object_text(object_text, objid=xref)


def _reconstruct_xref_object(document: object, xref: int) -> object | None:
    try:
        keys = document.xref_get_keys(xref)
    except Exception:
        return None
    if not keys:
        return None

    items: dict[str, object] = {}
    for key in keys:
        kind, value = document.xref_get_key(xref, key)
        parsed = _parse_xref_key_value(kind, value)
        if parsed is not _MISSING:
            items[key] = parsed

    return object_dict(items, objid=xref)


class _MissingValue:
    pass


_MISSING = _MissingValue()


def _parse_xref_key_value(kind: str, value: str) -> object:
    if kind == "null":
        return _MISSING
    if kind in {"xref", "array", "dict", "name", "int", "float", "real"}:
        return parse_object_text(value)
    if kind == "bool":
        return value == "true"
    if kind == "string":
        return parse_object_text(value)
    return parse_object_text(value)
