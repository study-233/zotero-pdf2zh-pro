from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from string import hexdigits
from typing import TYPE_CHECKING

from babeldoc.format.pdf.new_parser.active_object_backend import create_active_literal
from babeldoc.format.pdf.new_parser.active_object_backend import create_active_stream

WHITESPACE = b" \t\n\r\x0c\x00"
DELIMITERS = b"()<>[]{}/%"
CONTENT_KEYWORDS = {
    "q",
    "Q",
    "cm",
    "w",
    "J",
    "j",
    "M",
    "d",
    "ri",
    "i",
    "gs",
    "m",
    "l",
    "c",
    "v",
    "y",
    "h",
    "re",
    "S",
    "s",
    "f",
    "F",
    "f*",
    "B",
    "B*",
    "b",
    "b*",
    "n",
    "W",
    "W*",
    "sh",
    "BI",
    "ID",
    "EI",
    "Do",
    "MP",
    "DP",
    "BMC",
    "BDC",
    "EMC",
    "BX",
    "EX",
    "BT",
    "ET",
    "Tc",
    "Tw",
    "Tz",
    "TL",
    "Tf",
    "Tr",
    "Ts",
    "Td",
    "TD",
    "Tm",
    "T*",
    "Tj",
    "TJ",
    "'",
    '"',
    "CS",
    "cs",
    "SC",
    "SCN",
    "sc",
    "scn",
    "G",
    "g",
    "RG",
    "rg",
    "K",
    "k",
    "d0",
    "d1",
    "true",
    "false",
    "null",
}
COMPOSITE_SPLIT_KEYWORDS = tuple(sorted(CONTENT_KEYWORDS, key=len, reverse=True))
MAX_PDF_TOKEN_NESTING_DEPTH = 128

if TYPE_CHECKING:
    from babeldoc.format.pdf.new_parser.active_value_access import PDFStream


@dataclass(frozen=True)
class PdfName:
    value: str


@dataclass(frozen=True)
class PdfString:
    raw: bytes
    is_hex: bool = False


@dataclass(frozen=True)
class PdfKeyword:
    value: str


@dataclass(frozen=True)
class PdfOperation:
    operands: list[object]
    operator: str


class TokenizerError(ValueError):
    pass


def decode_pdf_name(value: str) -> str:
    result: list[str] = []
    index = 0
    while index < len(value):
        if (
            value[index] == "#"
            and index + 2 < len(value)
            and value[index + 1] in hexdigits
            and value[index + 2] in hexdigits
        ):
            result.append(chr(int(value[index + 1 : index + 3], 16)))
            index += 3
            continue
        result.append(value[index])
        index += 1
    return "".join(result)


def canonical_pdf_name(value: str) -> str:
    current = value
    while True:
        decoded = decode_pdf_name(current)
        if decoded == current:
            return current
        current = decoded


_NO_TOKEN = object()


class ContentStreamTokenizer:
    def __init__(
        self,
        data: bytes,
        *,
        recover_trailing_composites: bool = False,
        max_nesting_depth: int = MAX_PDF_TOKEN_NESTING_DEPTH,
    ) -> None:
        self.data = data
        self.pos = 0
        self.recover_trailing_composites = recover_trailing_composites
        self.max_nesting_depth = max_nesting_depth
        self.pending_tokens: deque[object] = deque()

    def iter_operation_stream(self):
        operands: list[object] = []
        while True:
            token = self._next_token()
            if token is None:
                break
            if isinstance(token, PdfKeyword):
                if token.value == "BI":
                    inline_stream = self._read_inline_image_stream()
                    yield PdfOperation(
                        operands=[inline_stream], operator="INLINE_IMAGE"
                    )
                    operands.clear()
                    continue
                yield PdfOperation(operands=operands.copy(), operator=token.value)
                operands.clear()
            else:
                operands.append(token)

    def iter_operations(self) -> list[PdfOperation]:
        return list(self.iter_operation_stream())

    def _read_inline_image_stream(self) -> PDFStream:
        inline_dict = self._read_inline_image_dict()
        inline_attrs = self._normalize_inline_image_value(inline_dict)
        length = self._inline_image_length(inline_dict)
        if length is not None:
            data_end = self.pos + length
            if data_end <= len(self.data):
                image_data = self.data[self.pos : data_end]
                self.pos = data_end
                if self._consume_inline_image_end():
                    return create_active_stream(inline_attrs, image_data)
        ei_pos = self._find_inline_image_end(self.pos)
        if ei_pos < 0:
            raise TokenizerError("Inline image missing EI delimiter.")
        image_data = self.data[self.pos : ei_pos]
        self.pos = ei_pos + 2
        return create_active_stream(inline_attrs, image_data)

    def _find_delimited_keyword(self, start: int, keyword: bytes) -> int:
        pos = start
        while True:
            pos = self.data.find(keyword, pos)
            if pos < 0:
                return -1
            prev_ok = pos == 0 or self.data[pos - 1 : pos] in WHITESPACE
            next_pos = pos + len(keyword)
            next_ok = (
                next_pos >= len(self.data)
                or self.data[next_pos : next_pos + 1] in WHITESPACE
            )
            if prev_ok and next_ok:
                return pos
            pos += 1

    def _find_inline_image_end(self, start: int) -> int:
        pos = start
        while True:
            pos = self.data.find(b"EI", pos)
            if pos < 0:
                return -1
            if not self._is_delimited_keyword_at(pos, b"EI"):
                pos += 1
                continue
            tail_start = pos + 2
            tail = self._read_next_nonspace_token_bytes(tail_start)
            if tail is None:
                return pos
            if tail[:1] in b"/":
                return pos
            if tail in {
                b"Q",
                b"q",
                b"cm",
                b"Do",
                b"BT",
                b"ET",
                b"W",
                b"W*",
                b"S",
                b"s",
                b"f",
                b"F",
                b"f*",
                b"B",
                b"B*",
                b"b",
                b"b*",
                b"n",
                b"BI",
                b"ID",
                b"EMC",
                b"BDC",
                b"Tj",
                b"TJ",
                b"Td",
                b"TD",
                b"Tm",
                b"Tf",
                b"TL",
                b"Tc",
                b"Tw",
                b"Tz",
                b"Ts",
                b"Tr",
                b"gs",
            }:
                return pos
            pos += 1

    def _is_delimited_keyword_at(self, pos: int, keyword: bytes) -> bool:
        if self.data[pos : pos + len(keyword)] != keyword:
            return False
        prev_ok = pos == 0 or self.data[pos - 1 : pos] in WHITESPACE
        next_pos = pos + len(keyword)
        next_ok = (
            next_pos >= len(self.data)
            or self.data[next_pos : next_pos + 1] in WHITESPACE
        )
        return prev_ok and next_ok

    def _read_next_nonspace_token_bytes(self, start: int) -> bytes | None:
        pos = start
        while pos < len(self.data):
            byte = self.data[pos : pos + 1]
            if byte in WHITESPACE:
                pos += 1
                continue
            if byte == b"%":
                while pos < len(self.data) and self.data[pos : pos + 1] not in b"\r\n":
                    pos += 1
                continue
            token_start = pos
            while pos < len(self.data):
                byte = self.data[pos : pos + 1]
                if byte in WHITESPACE or byte in DELIMITERS:
                    break
                pos += 1
            if pos == token_start:
                return self.data[pos : pos + 1]
            return self.data[token_start:pos]
        return None

    def _read_inline_image_dict(self) -> dict[str, object]:
        inline_dict: dict[str, object] = {}
        while True:
            self._skip_space_and_comments()
            if self.pos >= len(self.data):
                raise TokenizerError("Inline image missing ID delimiter.")
            if self._is_delimited_keyword_at(self.pos, b"ID"):
                self.pos += 2
                if (
                    self.pos < len(self.data)
                    and self.data[self.pos : self.pos + 1] in WHITESPACE
                ):
                    self.pos += 1
                return inline_dict

            key_token = self._next_token()
            if isinstance(key_token, PdfKeyword) and key_token.value == "ID":
                if (
                    self.pos < len(self.data)
                    and self.data[self.pos : self.pos + 1] in WHITESPACE
                ):
                    self.pos += 1
                return inline_dict
            key = self._inline_image_key_name(key_token)
            if key is None:
                raise TokenizerError("Inline image dictionary key missing.")
            value = self._next_token()
            if value is None:
                raise TokenizerError("Inline image dictionary value missing.")
            inline_dict[key] = value

    def _inline_image_key_name(self, token: object) -> str | None:
        if isinstance(token, PdfName):
            return token.value
        if isinstance(token, PdfKeyword):
            return token.value
        return None

    def _inline_image_length(self, inline_dict: dict[str, object]) -> int | None:
        for key in ("L", "Length"):
            value = inline_dict.get(key)
            if isinstance(value, int) and value >= 0:
                return value
        return None

    def _normalize_inline_image_value(self, value: object) -> object:
        if isinstance(value, PdfName):
            return create_active_literal(value.value)
        if isinstance(value, PdfString):
            return value.raw
        if isinstance(value, list):
            return [self._normalize_inline_image_value(item) for item in value]
        if isinstance(value, dict):
            normalized: dict[str, object] = {}
            for key, item in value.items():
                if isinstance(key, PdfName):
                    normalized_key = key.value
                elif isinstance(key, PdfKeyword):
                    normalized_key = key.value
                else:
                    normalized_key = str(key)
                normalized[normalized_key] = self._normalize_inline_image_value(item)
            return normalized
        return value

    def _consume_inline_image_end(self) -> bool:
        probe = self.pos
        while probe < len(self.data) and self.data[probe : probe + 1] in WHITESPACE:
            probe += 1
        if probe >= len(self.data):
            self.pos = probe
            return True
        if not self._is_delimited_keyword_at(probe, b"EI"):
            return False
        self.pos = probe + 2
        return True

    def _check_nesting_depth(self, depth: int) -> None:
        if depth > self.max_nesting_depth:
            msg = f"PDF token nesting exceeded max depth {self.max_nesting_depth}"
            raise TokenizerError(msg)

    def _next_token(self, *, depth: int = 0):
        self._check_nesting_depth(depth)
        if self.pending_tokens:
            return self.pending_tokens.popleft()
        while True:
            self._skip_space_and_comments()
            if self.pos >= len(self.data):
                return None

            byte = self.data[self.pos : self.pos + 1]
            if byte == b"/":
                return self._read_name()
            if byte == b"(":
                return self._read_literal_string()
            if byte == b"<":
                if self._peek(2) == b"<<":
                    return self._read_dictionary(depth=depth + 1)
                return self._read_hex_string()
            if byte == b"[":
                return self._read_array(depth=depth + 1)
            if byte in b"+-.0123456789":
                return self._recover_token(self._read_number_or_keyword(depth=depth))
            if byte in b"]>}":
                if self.recover_trailing_composites and byte in b"]>":
                    self.pos += 1
                    continue
                raise TokenizerError(f"Unexpected delimiter {byte!r}.")
            return self._recover_token(self._read_keyword())

    def _peek(self, count: int) -> bytes:
        return self.data[self.pos : self.pos + count]

    def _skip_space_and_comments(self) -> None:
        while self.pos < len(self.data):
            byte = self.data[self.pos : self.pos + 1]
            if byte in WHITESPACE:
                self.pos += 1
                continue
            if byte == b"%":
                while (
                    self.pos < len(self.data)
                    and self.data[self.pos : self.pos + 1] not in b"\r\n"
                ):
                    self.pos += 1
                continue
            break

    def _read_name(self) -> PdfName:
        self.pos += 1
        start = self.pos
        while self.pos < len(self.data):
            byte = self.data[self.pos : self.pos + 1]
            if byte in WHITESPACE or byte in DELIMITERS:
                break
            self.pos += 1
        return PdfName(self.data[start : self.pos].decode("latin-1"))

    def _read_literal_string(self) -> PdfString:
        self.pos += 1
        depth = 1
        out = bytearray()
        while self.pos < len(self.data):
            byte = self.data[self.pos]
            self.pos += 1
            if byte == 0x5C:  # backslash
                if self.pos >= len(self.data):
                    break
                escaped = self.data[self.pos]
                self.pos += 1
                escape_map = {
                    ord(b"n"): b"\n",
                    ord(b"r"): b"\r",
                    ord(b"t"): b"\t",
                    ord(b"b"): b"\b",
                    ord(b"f"): b"\f",
                    ord(b"("): b"(",
                    ord(b")"): b")",
                    ord(b"\\"): b"\\",
                }
                if escaped in escape_map:
                    out.extend(escape_map[escaped])
                    continue
                if escaped in b"\r\n":
                    if (
                        escaped == ord(b"\r")
                        and self.pos < len(self.data)
                        and self.data[self.pos] == ord(b"\n")
                    ):
                        self.pos += 1
                    continue
                if escaped in b"01234567":
                    octal_digits = bytearray([escaped])
                    for _ in range(2):
                        if self.pos >= len(self.data):
                            break
                        next_byte = self.data[self.pos]
                        if next_byte not in b"01234567":
                            break
                        octal_digits.append(next_byte)
                        self.pos += 1
                    out.append(int(octal_digits.decode("ascii"), 8))
                    continue
                out.append(escaped)
                continue
            if byte == 0x28:  # (
                depth += 1
                out.append(byte)
                continue
            if byte == 0x29:  # )
                depth -= 1
                if depth == 0:
                    return PdfString(bytes(out))
                out.append(byte)
                continue
            out.append(byte)
        raise TokenizerError("Unterminated literal string.")

    def _read_hex_string(self) -> PdfString:
        self.pos += 1
        start = self.pos
        while self.pos < len(self.data) and self.data[self.pos : self.pos + 1] != b">":
            self.pos += 1
        if self.pos >= len(self.data):
            raise TokenizerError("Unterminated hex string.")
        raw = b"".join(self.data[start : self.pos].split())
        self.pos += 1
        if len(raw) % 2 == 1:
            raw += b"0"
        return PdfString(bytes.fromhex(raw.decode("ascii")), is_hex=True)

    def _read_array(self, *, depth: int = 0) -> list[object]:
        self._check_nesting_depth(depth)
        self.pos += 1
        items: list[object] = []
        while True:
            self._skip_space_and_comments()
            if self.pos >= len(self.data):
                if self.recover_trailing_composites:
                    return items
                raise TokenizerError("Unterminated array.")
            if self.data[self.pos : self.pos + 1] == b"]":
                self.pos += 1
                return items
            items.append(self._next_token(depth=depth))

    def _read_dictionary(self, *, depth: int = 0) -> dict[str, object]:
        self._check_nesting_depth(depth)
        if self._peek(2) != b"<<":
            raise TokenizerError("Dictionary must start with <<.")
        self.pos += 2
        items: dict[str, object] = {}
        while True:
            self._skip_space_and_comments()
            if self.pos >= len(self.data):
                if self.recover_trailing_composites:
                    return items
                raise TokenizerError("Unterminated dictionary.")
            if self._peek(2) == b">>":
                self.pos += 2
                return items
            key = self._next_token(depth=depth)
            if not isinstance(key, PdfName):
                raise TokenizerError(
                    f"Dictionary key must be PdfName, got {type(key)}."
                )
            value = self._next_token(depth=depth)
            if value is None:
                if self.recover_trailing_composites and self.pos >= len(self.data):
                    return items
                raise TokenizerError("Dictionary value missing.")
            items[key.value] = value

    def _read_number_or_keyword(self, *, depth: int = 0):
        numeric_token = self._read_numeric_token()
        if numeric_token is not _NO_TOKEN:
            if numeric_token is None:
                return self._next_token(depth=depth)
            return numeric_token

        start = self.pos
        while self.pos < len(self.data):
            byte = self.data[self.pos : self.pos + 1]
            if byte in WHITESPACE or byte in DELIMITERS:
                break
            self.pos += 1
        raw = self.data[start : self.pos].decode("latin-1")
        if raw in {"true", "false", "null"}:
            return {"true": True, "false": False, "null": None}[raw]
        try:
            if "." in raw or raw in {"+", "-"}:
                raise ValueError
            return int(raw)
        except ValueError:
            try:
                return float(raw)
            except ValueError:
                return PdfKeyword(raw)

    def _read_numeric_token(self):
        if self.pos >= len(self.data):
            return _NO_TOKEN

        start = self.pos
        byte = self.data[start : start + 1]
        pos = start

        if byte in b"+-":
            pos += 1
        if pos < len(self.data) and self.data[pos : pos + 1].isdigit():
            while pos < len(self.data) and self.data[pos : pos + 1].isdigit():
                pos += 1
        if pos < len(self.data) and self.data[pos : pos + 1] == b".":
            pos += 1
            while pos < len(self.data) and self.data[pos : pos + 1].isdigit():
                pos += 1
        if pos == start:
            return _NO_TOKEN

        raw = self.data[start:pos].decode("latin-1")
        self.pos = pos
        if raw in {"+", "-", ".", "+.", "-."}:
            return None
        if "." in raw:
            return float(raw)
        return int(raw)

    def _read_keyword(self) -> PdfKeyword:
        start = self.pos
        while self.pos < len(self.data):
            byte = self.data[self.pos : self.pos + 1]
            if byte in WHITESPACE or byte in DELIMITERS:
                break
            self.pos += 1
        return PdfKeyword(self.data[start : self.pos].decode("latin-1"))

    def _recover_token(self, token: object):
        if not isinstance(token, PdfKeyword):
            return token
        composite = self._split_composite_keyword(token.value)
        if composite is None:
            return token
        if len(composite) > 1:
            self.pending_tokens.extend(composite[1:])
        return composite[0]

    def _split_composite_keyword(self, raw: str) -> list[object] | None:
        if raw in CONTENT_KEYWORDS:
            return None
        if not any(ch.isdigit() for ch in raw):
            return None

        tokens: list[object] = []
        pos = 0
        while pos < len(raw):
            fragment = raw[pos:]
            numeric = self._parse_numeric_prefix(fragment)
            if numeric is not _NO_TOKEN:
                if numeric is None:
                    pos += 1
                    continue
                value, consumed = numeric
                tokens.append(value)
                pos += consumed
                continue

            matched_keyword = None
            for keyword in COMPOSITE_SPLIT_KEYWORDS:
                if raw.startswith(keyword, pos):
                    matched_keyword = keyword
                    break
            if matched_keyword is None:
                return None
            tokens.append(PdfKeyword(matched_keyword))
            pos += len(matched_keyword)

        if len(tokens) <= 1:
            return None
        return tokens

    def _parse_numeric_prefix(self, raw: str):
        if not raw:
            return _NO_TOKEN
        pos = 0
        if raw[pos] in "+-":
            pos += 1
        start_digits = pos
        while pos < len(raw) and raw[pos].isdigit():
            pos += 1
        if pos < len(raw) and raw[pos] == ".":
            pos += 1
            while pos < len(raw) and raw[pos].isdigit():
                pos += 1
        if (
            pos == 0
            or (pos == 1 and raw[0] in "+-")
            or (pos == start_digits and raw[:1] == ".")
        ):
            return _NO_TOKEN
        numeric_raw = raw[:pos]
        if numeric_raw in {"+", "-", ".", "+.", "-."}:
            return None
        if "." in numeric_raw:
            return float(numeric_raw), pos
        return int(numeric_raw), pos


def tokenize_operations(data: bytes) -> list[PdfOperation]:
    return ContentStreamTokenizer(data).iter_operations()
