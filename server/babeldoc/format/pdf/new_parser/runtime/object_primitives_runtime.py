from __future__ import annotations

from babeldoc.format.pdf.new_parser.object_model import PdfIndirectRef
from babeldoc.format.pdf.new_parser.object_model import PdfObjectStream
from babeldoc.format.pdf.new_parser.runtime.exceptions_runtime import PDFTypeError
from babeldoc.format.pdf.new_parser.runtime.object_stream_runtime import (
    RuntimePdfStream,
)
from babeldoc.format.pdf.new_parser.runtime.ps_primitives_runtime import LIT
from babeldoc.format.pdf.new_parser.runtime.ps_primitives_runtime import (
    literal_name as _literal_name,
)
from babeldoc.format.pdf.new_parser.runtime.runtime_settings import STRICT

LITERAL_FONT = LIT("Font")


def resolve1(value: object, default: object = None):
    while isinstance(value, PdfIndirectRef):
        value = value.resolve(default=default)
    while hasattr(value, "resolve") and hasattr(value, "objid"):
        value = value.resolve(default=default)
    return value


def dict_value(value: object):
    if isinstance(value, dict):
        return value
    value = resolve1(value)
    if not isinstance(value, dict):
        if STRICT:
            raise PDFTypeError(f"Dict required: {value!r}")
        return {}
    return value


def list_value(value: object):
    if isinstance(value, list):
        return value
    value = resolve1(value)
    if not isinstance(value, list | tuple):
        if STRICT:
            raise PDFTypeError(f"List required: {value!r}")
        return []
    return value


def stream_value(value: object):
    if isinstance(value, PdfObjectStream | RuntimePdfStream):
        return value
    value = resolve1(value)
    if isinstance(value, PdfObjectStream | RuntimePdfStream):
        return value
    if hasattr(value, "get_data") and hasattr(value, "get"):
        return value
    if STRICT:
        raise PDFTypeError(f"PDFStream required: {value!r}")
    return RuntimePdfStream({}, b"")


def int_value(value: object) -> int:
    if isinstance(value, int):
        return value
    value = resolve1(value)
    if not isinstance(value, int):
        if STRICT:
            raise PDFTypeError(f"Integer required: {value!r}")
        return 0
    return value


def num_value(value: object) -> float:
    if isinstance(value, int | float):
        return float(value)
    value = resolve1(value)
    if not isinstance(value, int | float):
        if STRICT:
            raise PDFTypeError(f"Int or Float required: {value!r}")
        return 0
    return float(value)


def literal_name(value: object):
    if isinstance(value, str):
        return value
    return _literal_name(value)


def obj_ref_id(value: object) -> int | None:
    if isinstance(value, PdfIndirectRef):
        return value.objid
    return (
        value.objid if hasattr(value, "objid") and hasattr(value, "resolve") else None
    )


def create_literal(name: str | bytes):
    return LIT(name)


def create_stream(attrs: dict[object, object], rawdata: bytes):
    return RuntimePdfStream(attrs, rawdata)


def font_type_literal():
    return LITERAL_FONT
