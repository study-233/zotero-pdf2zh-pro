from __future__ import annotations

from babeldoc.format.pdf.new_parser.runtime.object_primitives_runtime import (
    font_type_literal,
)

DIRECT_FONT_SUBTYPES = frozenset(
    {
        "Type0",
        "Type1",
        "MMType1",
        "TrueType",
        "Type3",
        "CIDFontType0",
        "CIDFontType2",
    }
)


def normalize_font_subtype(subtype: object) -> object:
    literal_name = getattr(subtype, "name", None)
    if isinstance(literal_name, bytes):
        return literal_name.decode("latin-1")
    if isinstance(literal_name, str):
        return literal_name
    return subtype


def classify_font_subtype(runtime_spec: dict[object, object]) -> str | None:
    subtype = normalize_font_subtype(runtime_spec.get("Subtype"))
    return subtype if isinstance(subtype, str) else None


def is_direct_font_subtype(runtime_spec: dict[object, object]) -> bool:
    subtype = classify_font_subtype(runtime_spec)
    return subtype in DIRECT_FONT_SUBTYPES


def ensure_font_type(runtime_spec: dict[object, object]) -> dict[object, object]:
    if "Type" in runtime_spec:
        return runtime_spec

    spec = dict(runtime_spec)
    spec["Type"] = font_type_literal()
    return spec
