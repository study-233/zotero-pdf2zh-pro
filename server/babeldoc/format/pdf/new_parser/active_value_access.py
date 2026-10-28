from __future__ import annotations

from babeldoc.format.pdf.new_parser.object_model import ActiveLiteral
from babeldoc.format.pdf.new_parser.runtime.object_primitives_runtime import (
    dict_value as _dict_value,
)
from babeldoc.format.pdf.new_parser.runtime.object_primitives_runtime import (
    int_value as _int_value,
)
from babeldoc.format.pdf.new_parser.runtime.object_primitives_runtime import (
    list_value as _list_value,
)
from babeldoc.format.pdf.new_parser.runtime.object_primitives_runtime import (
    literal_name as _literal_name,
)
from babeldoc.format.pdf.new_parser.runtime.object_primitives_runtime import (
    num_value as _num_value,
)
from babeldoc.format.pdf.new_parser.runtime.object_primitives_runtime import (
    obj_ref_id as _obj_ref_id,
)
from babeldoc.format.pdf.new_parser.runtime.object_primitives_runtime import (
    stream_value as _stream_value,
)


def dict_value(value: object):
    return _dict_value(value)


def list_value(value: object):
    return _list_value(value)


def int_value(value: object):
    return _int_value(value)


def num_value(value: object):
    return _num_value(value)


def stream_value(value: object):
    return _stream_value(value)


def literal_name(value: object):
    if isinstance(value, ActiveLiteral):
        literal = value.name
        return literal.decode("latin-1") if isinstance(literal, bytes) else literal
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("latin-1")
    return _literal_name(value)


def obj_ref_id(value: object) -> int | None:
    return _obj_ref_id(value)
