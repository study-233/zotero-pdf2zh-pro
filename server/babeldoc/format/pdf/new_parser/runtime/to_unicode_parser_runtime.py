from __future__ import annotations

import re
import struct
from collections.abc import Iterator

from babeldoc.format.pdf.new_parser.runtime.ps_primitives_runtime import PSLiteral

_TOKEN_RE = re.compile(
    rb"<[0-9A-Fa-f\s]*>|/[^\s<>\[\]{}()/%]+|[-+]?\d+|[A-Za-z][A-Za-z0-9._-]*|[\[\]]"
)
ARRAY_BEGIN_TOKEN = b"["
ARRAY_END_TOKEN = b"]"
ARRAY_BEGIN_SENTINEL = object()
ARRAY_END_SENTINEL = object()
END_CID_RANGE_TOKEN = b"endcidrange"
END_CID_CHAR_TOKEN = b"endcidchar"
END_BF_RANGE_TOKEN = b"endbfrange"
END_BF_CHAR_TOKEN = b"endbfchar"


def parse_tounicode_stream(data: bytes, unicode_map: object) -> object:
    stack: list[object] = []
    array_stack: list[list[object]] = []

    for token in _iter_tokens(data):
        if token is ARRAY_BEGIN_SENTINEL:
            array_stack.append([])
            continue
        if token is ARRAY_END_SENTINEL:
            if not array_stack:
                continue
            items = array_stack.pop()
            _push_token(stack, array_stack, items)
            continue
        if isinstance(token, bytes):
            if token == END_CID_RANGE_TOKEN:
                _apply_cidrange(unicode_map, stack)
                stack.clear()
                continue
            if token == END_CID_CHAR_TOKEN:
                _apply_cidchar(unicode_map, stack)
                stack.clear()
                continue
            if token == END_BF_RANGE_TOKEN:
                _apply_bfrange(unicode_map, stack)
                stack.clear()
                continue
            if token == END_BF_CHAR_TOKEN:
                _apply_bfchar(unicode_map, stack)
                stack.clear()
                continue
            if token.startswith(b"begin") or token.startswith(b"end"):
                stack.clear()
                continue
        _push_token(stack, array_stack, token)
    return unicode_map


def _push_token(
    stack: list[object], array_stack: list[list[object]], token: object
) -> None:
    if array_stack:
        array_stack[-1].append(token)
    else:
        stack.append(token)


def _iter_tokens(data: bytes) -> Iterator[object]:
    for raw_token in _TOKEN_RE.findall(data):
        if raw_token == b"[":
            yield ARRAY_BEGIN_SENTINEL
            continue
        if raw_token == b"]":
            yield ARRAY_END_SENTINEL
            continue
        if raw_token.startswith(b"/"):
            try:
                name = raw_token[1:].decode("utf-8")
            except UnicodeDecodeError:
                name = raw_token[1:]
            yield PSLiteral(name)
            continue
        if raw_token.startswith(b"<") and raw_token.endswith(b">"):
            hex_data = re.sub(rb"\s+", b"", raw_token[1:-1])
            if len(hex_data) % 2 == 1:
                hex_data += b"0"
            try:
                yield bytes.fromhex(hex_data.decode("ascii"))
            except ValueError:
                continue
            continue
        if raw_token[:1].isdigit() or raw_token[:1] in {b"+", b"-"}:
            try:
                yield int(raw_token)
            except ValueError:
                continue
            continue
        try:
            yield bytes(raw_token)
        except Exception:
            continue


def _apply_cidrange(unicode_map: object, stack: list[object]) -> None:
    for start_byte, end_byte, cid in _chop(3, stack):
        if not isinstance(start_byte, bytes | bytearray):
            continue
        if not isinstance(end_byte, bytes | bytearray):
            continue
        if not isinstance(cid, int):
            continue
        if len(start_byte) != len(end_byte):
            continue
        start_prefix = bytes(start_byte[:-4])
        end_prefix = bytes(end_byte[:-4])
        if start_prefix != end_prefix:
            continue
        start_var = bytes(start_byte[-4:])
        end_var = bytes(end_byte[-4:])
        start = _nunpack(start_var)
        end = _nunpack(end_var)
        value_len = len(start_var)
        for index in range(end - start + 1):
            value = start_prefix + struct.pack(">L", start + index)[-value_len:]
            unicode_map.add_cid2unichr(cid + index, value)


def _apply_cidchar(unicode_map: object, stack: list[object]) -> None:
    for cid, code in _chop(2, stack):
        if isinstance(cid, int) and isinstance(code, bytes):
            unicode_map.add_cid2unichr(cid, code)


def _apply_bfrange(unicode_map: object, stack: list[object]) -> None:
    for start_byte, end_byte, code in _chop(3, stack):
        if not isinstance(start_byte, bytes | bytearray):
            continue
        if not isinstance(end_byte, bytes | bytearray):
            continue
        if len(start_byte) != len(end_byte):
            continue
        start = _nunpack(bytes(start_byte))
        end = _nunpack(bytes(end_byte))
        if isinstance(code, list):
            for cid, unicode_value in zip(range(start, end + 1), code, strict=False):
                unicode_map.add_cid2unichr(cid, unicode_value)
            continue
        if not isinstance(code, bytes):
            continue
        value = code[-4:]
        base = _nunpack(value)
        prefix = code[:-4]
        value_len = len(value)
        for index in range(end - start + 1):
            mapped = prefix + struct.pack(">L", base + index)[-value_len:]
            unicode_map.add_cid2unichr(start + index, mapped)


def _apply_bfchar(unicode_map: object, stack: list[object]) -> None:
    for cid, code in _chop(2, stack):
        if isinstance(cid, bytes) and isinstance(code, bytes):
            unicode_map.add_cid2unichr(_nunpack(cid), code)


def _chop(size: int, values: list[object]):
    for index in range(0, len(values), size):
        if index + size <= len(values):
            yield tuple(values[index : index + size])


def _nunpack(data: bytes) -> int:
    value = 0
    for byte in data:
        value = (value << 8) | byte
    return value
