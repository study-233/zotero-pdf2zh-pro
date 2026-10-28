from __future__ import annotations

import re
import struct
from typing import BinaryIO
from typing import cast

import freetype

from babeldoc.format.pdf.new_parser.runtime.cmap_types_runtime import FileUnicodeMap
from babeldoc.format.pdf.new_parser.runtime.data.fontmetrics import FONT_METRICS
from babeldoc.format.pdf.new_parser.runtime.exceptions_runtime import PDFException
from babeldoc.format.pdf.new_parser.runtime.font_encoding_runtime import name2unicode
from babeldoc.format.pdf.new_parser.runtime.ps_primitives_runtime import PSLiteral
from babeldoc.format.pdf.new_parser.runtime.ps_primitives_runtime import literal_name

_TOKEN_RE = re.compile(
    rb"/[^\s<>\[\]{}()/%]+|[-+]?\d+|[A-Za-z][A-Za-z0-9._-]*|[][(){}<>%]"
)
TYPE1_PUT_OPERATOR = "put"


class FontMetricsDB:
    @classmethod
    def get_metrics(cls, fontname: str) -> tuple[dict[str, object], dict[str, int]]:
        return FONT_METRICS[fontname]


class TrueTypeFont:
    class CMapNotFound(PDFException):
        pass

    def __init__(self, name: str, fp: BinaryIO) -> None:
        self.name = name
        self.fp = fp
        self.tables: dict[bytes, tuple[int, int]] = {}
        self.fonttype = fp.read(4)
        try:
            ntables, _, _, _ = cast(
                tuple[int, int, int, int], struct.unpack(">HHHH", fp.read(8))
            )
            for _ in range(ntables):
                name_bytes, _, offset, length = cast(
                    tuple[bytes, int, int, int],
                    struct.unpack(">4sLLL", fp.read(16)),
                )
                self.tables[name_bytes] = (offset, length)
        except struct.error:
            pass

    def create_unicode_map(self):
        if b"cmap" not in self.tables:
            raise TrueTypeFont.CMapNotFound
        try:
            face = freetype.Face(self.fp)
            char2gid = list(face.get_chars())
        except Exception as exc:
            raise TrueTypeFont.CMapNotFound from exc
        unicode_map = FileUnicodeMap()
        for char, gid in char2gid:
            unicode_map.add_cid2unichr(gid, char)
        return unicode_map


class Type1FontHeaderParser:
    def __init__(self, data: BinaryIO) -> None:
        self._data = data
        self._cid2unicode: dict[int, str] = {}

    def get_encoding(self) -> dict[int, str]:
        stack: list[object] = []
        for token in _iter_type1_header_tokens(self._data.read()):
            if token == TYPE1_PUT_OPERATOR:
                if len(stack) < 2:
                    continue
                key = stack[-2]
                value = stack[-1]
                del stack[-2:]
                if isinstance(key, int) and isinstance(value, PSLiteral):
                    try:
                        self._cid2unicode[key] = name2unicode(literal_name(value))
                    except KeyError:
                        pass
                continue
            stack.append(token)
        return self._cid2unicode


def _iter_type1_header_tokens(data: bytes):
    for raw_token in _TOKEN_RE.findall(data):
        if raw_token.startswith(b"/"):
            try:
                literal = raw_token[1:].decode("utf-8")
            except UnicodeDecodeError:
                literal = raw_token[1:]
            yield PSLiteral(literal)
            continue
        if raw_token[:1] in {b"[", b"]", b"(", b")", b"{", b"}", b"<", b">", b"%"}:
            continue
        if raw_token[:1].isdigit() or raw_token[:1] in {b"+", b"-"}:
            try:
                yield int(raw_token)
            except ValueError:
                continue
            continue
        try:
            yield raw_token.decode("ascii")
        except UnicodeDecodeError:
            continue
