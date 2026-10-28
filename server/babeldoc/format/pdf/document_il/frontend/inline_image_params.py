"""Helpers for preserving inline image dictionaries in IL.

Inline image dictionaries are later embedded directly back into a PDF content
stream.  Keep values as PDF tokens instead of Python repr strings.
"""

from __future__ import annotations

from babeldoc.format.pdf.new_parser.pdf_token_serializer import (
    normalize_pdf_token_value,
)


def normalize_inline_image_parameters(image_dict: object) -> dict[str, object]:
    if not isinstance(image_dict, dict):
        return {}

    parameters: dict[str, object] = {}
    for key, value in image_dict.items():
        key_name = _inline_name(key)
        if key_name is None:
            continue
        parameters[key_name] = _inline_value(value)
    return parameters


def _inline_name(value: object) -> str | None:
    if isinstance(value, str):
        return value[1:] if value.startswith("/") else value
    name = getattr(value, "name", None)
    if name is not None:
        return _decode_name(name)
    token_value = getattr(value, "value", None)
    if isinstance(token_value, str):
        return token_value
    return None


def _inline_value(value: object) -> object:
    if isinstance(value, list | tuple):
        return [_inline_value(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _inline_value(item)
            for key, item in normalize_inline_image_parameters(value).items()
        }

    return normalize_pdf_token_value(value)


def _decode_name(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("latin-1")
    return str(value)
