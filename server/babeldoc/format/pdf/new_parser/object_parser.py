from __future__ import annotations

from dataclasses import dataclass

from babeldoc.format.pdf.new_parser.object_model import PdfIndirectRef
from babeldoc.format.pdf.new_parser.tokenizer import ContentStreamTokenizer
from babeldoc.format.pdf.new_parser.tokenizer import PdfKeyword
from babeldoc.format.pdf.new_parser.tokenizer import PdfName
from babeldoc.format.pdf.new_parser.tokenizer import PdfString
from babeldoc.format.pdf.new_parser.tokenizer import TokenizerError
from babeldoc.format.pdf.new_parser.tokenizer import decode_pdf_name

MAX_OBJECT_NESTING_DEPTH = 128


class ObjectParserError(ValueError):
    pass


@dataclass(slots=True)
class _Parser:
    lexer: ContentStreamTokenizer
    max_nesting_depth: int = MAX_OBJECT_NESTING_DEPTH

    def parse(self) -> object:
        value = self._parse_value()
        self.lexer._skip_space_and_comments()  # noqa: SLF001
        if self.lexer.pos != len(self.lexer.data):
            tail = self.lexer.data[self.lexer.pos : self.lexer.pos + 32]
            raise ObjectParserError(f"Trailing data after object: {tail!r}")
        return value

    def _check_nesting_depth(self, depth: int) -> None:
        if depth > self.max_nesting_depth:
            msg = f"PDF object nesting exceeded max depth {self.max_nesting_depth}"
            raise ObjectParserError(msg)

    def _parse_value(self, *, depth: int = 0) -> object:
        self._check_nesting_depth(depth)
        self.lexer._skip_space_and_comments()  # noqa: SLF001
        if self.lexer.pos >= len(self.lexer.data):
            raise ObjectParserError("Unexpected EOF while parsing object.")

        token = self.lexer.data[self.lexer.pos : self.lexer.pos + 1]
        if token == b"<" and self.lexer._peek(2) == b"<<":  # noqa: SLF001
            return self._parse_dict(depth=depth + 1)
        if token == b"[":
            return self._parse_array(depth=depth + 1)
        if token == b"/":
            return decode_pdf_name(self.lexer._read_name().value)  # noqa: SLF001
        if token == b"(":
            return self.lexer._read_literal_string().raw  # noqa: SLF001
        if token == b"<":
            return self.lexer._read_hex_string().raw  # noqa: SLF001

        raw = self.lexer._read_number_or_keyword()  # noqa: SLF001
        return self._maybe_indirect_ref(raw)

    def _parse_array(self, *, depth: int = 0) -> list[object]:
        self._check_nesting_depth(depth)
        if self.lexer.data[self.lexer.pos : self.lexer.pos + 1] != b"[":
            raise ObjectParserError("Array must start with '['.")
        self.lexer.pos += 1
        items: list[object] = []
        while True:
            self.lexer._skip_space_and_comments()  # noqa: SLF001
            if self.lexer.pos >= len(self.lexer.data):
                raise ObjectParserError("Unterminated array.")
            if self.lexer.data[self.lexer.pos : self.lexer.pos + 1] == b"]":
                self.lexer.pos += 1
                return items
            items.append(self._parse_value(depth=depth))

    def _parse_dict(self, *, depth: int = 0) -> dict[str, object]:
        self._check_nesting_depth(depth)
        if self.lexer._peek(2) != b"<<":  # noqa: SLF001
            raise ObjectParserError("Dictionary must start with '<<'.")
        self.lexer.pos += 2
        items: dict[str, object] = {}
        while True:
            self.lexer._skip_space_and_comments()  # noqa: SLF001
            if self.lexer.pos >= len(self.lexer.data):
                raise ObjectParserError("Unterminated dictionary.")
            if self.lexer._peek(2) == b">>":  # noqa: SLF001
                self.lexer.pos += 2
                return items

            key = self.lexer._read_name()  # noqa: SLF001
            if not isinstance(key, PdfName):
                raise ObjectParserError(f"Dictionary key must be PdfName, got {key!r}")
            items[key.value] = self._parse_value(depth=depth)

    def _maybe_indirect_ref(self, first: object) -> object:
        if not isinstance(first, int):
            return self._normalize_scalar(first)

        saved_pos = self.lexer.pos
        self.lexer._skip_space_and_comments()  # noqa: SLF001
        second = self.lexer._read_number_or_keyword()  # noqa: SLF001
        if not isinstance(second, int):
            self.lexer.pos = saved_pos
            return first

        self.lexer._skip_space_and_comments()  # noqa: SLF001
        third = self.lexer._read_keyword()  # noqa: SLF001
        if isinstance(third, PdfKeyword) and third.value == "R":
            return PdfIndirectRef(objid=first, generation=second)

        self.lexer.pos = saved_pos
        return first

    def _normalize_scalar(self, value: object) -> object:
        if isinstance(value, PdfName):
            return value.value
        if isinstance(value, PdfString):
            return value.raw
        return value


def parse_object_bytes(
    data: bytes,
    *,
    max_nesting_depth: int = MAX_OBJECT_NESTING_DEPTH,
) -> object:
    parser = _Parser(
        ContentStreamTokenizer(data, max_nesting_depth=max_nesting_depth),
        max_nesting_depth=max_nesting_depth,
    )
    try:
        return parser.parse()
    except TokenizerError as exc:
        raise ObjectParserError(str(exc)) from exc


__all__ = ["ObjectParserError", "PdfIndirectRef", "parse_object_bytes"]
