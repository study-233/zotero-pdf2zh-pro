from __future__ import annotations

from numbers import Real

from babeldoc.format.pdf.new_parser.object_model import ActiveLiteral
from babeldoc.format.pdf.new_parser.object_model import PdfIndirectRef
from babeldoc.format.pdf.new_parser.object_model import PdfObjectStream
from babeldoc.format.pdf.new_parser.tokenizer import PdfKeyword
from babeldoc.format.pdf.new_parser.tokenizer import PdfName
from babeldoc.format.pdf.new_parser.tokenizer import PdfString

PDF_KEYWORDS = {"true": True, "false": False, "null": None}
MAX_PDF_TOKEN_SERIALIZATION_DEPTH = 128


class PdfTokenSerializationError(ValueError):
    pass


def serialize_pdf_token(
    value: object,
    *,
    max_depth: int = MAX_PDF_TOKEN_SERIALIZATION_DEPTH,
) -> str:
    """Serialize known parser PDF token objects for content-stream output."""
    return _serialize_pdf_token(value, max_depth=max_depth)


def _check_depth(depth: int, max_depth: int) -> None:
    if depth > max_depth:
        msg = f"PDF token serialization exceeded max depth {max_depth}"
        raise PdfTokenSerializationError(msg)


def _serialize_pdf_token(
    value: object,
    *,
    max_depth: int,
    depth: int = 0,
    active_containers: frozenset[int] = frozenset(),
) -> str:
    _check_depth(depth, max_depth)
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    if isinstance(value, Real):
        if isinstance(value, int):
            return str(value)
        return f"{float(value):f}"
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return f"<{value.hex().upper()}>"
    if isinstance(value, PdfName):
        return f"/{value.value}"
    if isinstance(value, ActiveLiteral):
        return f"/{_decode_name(value.name)}"
    if isinstance(value, PdfKeyword):
        return value.value
    if _is_ps_literal(value):
        return f"/{_decode_name(value.name)}"
    if isinstance(value, PdfIndirectRef):
        return f"{value.objid} {value.generation} R"
    if isinstance(value, PdfString):
        return _serialize_pdf_string(value)
    if isinstance(value, list | tuple):
        container_id = id(value)
        if container_id in active_containers:
            msg = "Recursive PDF token sequence"
            raise PdfTokenSerializationError(msg)
        return (
            "["
            + " ".join(
                _serialize_pdf_token(
                    item,
                    max_depth=max_depth,
                    depth=depth + 1,
                    active_containers=active_containers | {container_id},
                )
                for item in value
            )
            + "]"
        )
    if isinstance(value, dict):
        container_id = id(value)
        if container_id in active_containers:
            msg = "Recursive PDF token dictionary"
            raise PdfTokenSerializationError(msg)
        parts = []
        for key, item in value.items():
            key_name = _dict_key_name(key)
            rendered_item = _serialize_pdf_token(
                item,
                max_depth=max_depth,
                depth=depth + 1,
                active_containers=active_containers | {container_id},
            )
            parts.append(f"/{key_name} {rendered_item}")
        return f"<< {' '.join(parts)} >>"
    if isinstance(value, PdfObjectStream):
        raise TypeError("PdfObjectStream cannot be serialized as a PDF token.")
    return str(value)


def normalize_pdf_token_value(
    value: object,
    *,
    max_depth: int = MAX_PDF_TOKEN_SERIALIZATION_DEPTH,
) -> object:
    """Return a JSON-friendly normalized representation for PDF token values."""
    return _normalize_pdf_token_value(value, max_depth=max_depth)


def _normalize_pdf_token_value(
    value: object,
    *,
    max_depth: int,
    depth: int = 0,
    active_containers: frozenset[int] = frozenset(),
) -> object:
    _check_depth(depth, max_depth)
    if isinstance(value, PdfKeyword) and value.value in PDF_KEYWORDS:
        return PDF_KEYWORDS[value.value]
    if isinstance(value, PdfName | ActiveLiteral) or _is_ps_literal(value):
        return _serialize_pdf_token(value, max_depth=max_depth, depth=depth)
    if isinstance(value, PdfIndirectRef | PdfString):
        return _serialize_pdf_token(value, max_depth=max_depth, depth=depth)
    if isinstance(value, bytes):
        return _serialize_pdf_token(value, max_depth=max_depth, depth=depth)
    if isinstance(value, list | tuple):
        container_id = id(value)
        if container_id in active_containers:
            msg = "Recursive normalized PDF token sequence"
            raise PdfTokenSerializationError(msg)
        return [
            _normalize_pdf_token_value(
                item,
                max_depth=max_depth,
                depth=depth + 1,
                active_containers=active_containers | {container_id},
            )
            for item in value
        ]
    if isinstance(value, dict):
        container_id = id(value)
        if container_id in active_containers:
            msg = "Recursive normalized PDF token dictionary"
            raise PdfTokenSerializationError(msg)
        return {
            _dict_key_name(key): _normalize_pdf_token_value(
                item,
                max_depth=max_depth,
                depth=depth + 1,
                active_containers=active_containers | {container_id},
            )
            for key, item in value.items()
        }
    return value


def _serialize_pdf_string(value: PdfString) -> str:
    if value.is_hex:
        return f"<{value.raw.hex().upper()}>"
    escaped = bytearray()
    escape_map = {
        0x08: b"\\b",
        0x09: b"\\t",
        0x0A: b"\\n",
        0x0C: b"\\f",
        0x0D: b"\\r",
        0x28: b"\\(",
        0x29: b"\\)",
        0x5C: b"\\\\",
    }
    for byte in value.raw:
        escaped.extend(escape_map.get(byte, bytes([byte])))
    return f"({escaped.decode('latin-1')})"


def _dict_key_name(value: object) -> str:
    if isinstance(value, PdfName):
        return value.value
    if isinstance(value, ActiveLiteral):
        return _decode_name(value.name)
    if _is_ps_literal(value):
        return _decode_name(value.name)
    if isinstance(value, str):
        return value[1:] if value.startswith("/") else value
    return str(value)


def _is_ps_literal(value: object) -> bool:
    return type(value).__name__ in {"PSLiteral", "PSKeyword"} and hasattr(value, "name")


def _decode_name(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("latin-1")
    return str(value)
