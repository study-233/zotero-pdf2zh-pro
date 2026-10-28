from __future__ import annotations

from babeldoc.format.pdf.new_parser.object_model import ActiveLiteral
from babeldoc.format.pdf.new_parser.runtime.object_primitives_runtime import (
    create_stream as _create_stream,
)


def create_active_literal(name: str | bytes):
    return ActiveLiteral(name)


def create_active_stream(attrs: dict[object, object], rawdata: bytes):
    return _create_stream(attrs, rawdata)
