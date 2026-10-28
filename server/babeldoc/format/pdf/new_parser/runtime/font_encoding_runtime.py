from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from typing import cast

from babeldoc.format.pdf.new_parser.runtime.data.glyphlist import glyphname2unicode
from babeldoc.format.pdf.new_parser.runtime.data.latin_enc import ENCODING
from babeldoc.format.pdf.new_parser.runtime.exceptions_runtime import PDFKeyError
from babeldoc.format.pdf.new_parser.runtime.ps_primitives_runtime import PSLiteral

HEXADECIMAL = re.compile(r"[0-9a-fA-F]+")

log = logging.getLogger(__name__)


def name2unicode(name: str) -> str:
    if not isinstance(name, str):
        raise PDFKeyError(
            f'Could not convert unicode name "{name}" to character because '
            f"it should be of type str but is of type {type(name)}",
        )

    name = name.split(".")[0]
    components = name.split("_")

    if len(components) > 1:
        return "".join(map(name2unicode, components))

    if name in glyphname2unicode:
        return glyphname2unicode[name]

    if name.startswith("uni"):
        name_without_uni = name.removeprefix("uni")
        if HEXADECIMAL.match(name_without_uni) and len(name_without_uni) % 4 == 0:
            unicode_digits = [
                int(name_without_uni[i : i + 4], base=16)
                for i in range(0, len(name_without_uni), 4)
            ]
            for digit in unicode_digits:
                _raise_key_error_for_invalid_unicode(digit)
            return "".join(map(chr, unicode_digits))

    if name.startswith("u"):
        name_without_u = name.removeprefix("u")
        if HEXADECIMAL.match(name_without_u) and 4 <= len(name_without_u) <= 6:
            unicode_digit = int(name_without_u, base=16)
            _raise_key_error_for_invalid_unicode(unicode_digit)
            return chr(unicode_digit)

    raise PDFKeyError(
        f'Could not convert unicode name "{name}" to character because '
        "it does not match specification",
    )


def _raise_key_error_for_invalid_unicode(unicode_digit: int) -> None:
    if 55295 < unicode_digit < 57344:
        raise PDFKeyError(
            f"Unicode digit {unicode_digit} is invalid because "
            "it is in the range D800 through DFFF",
        )


class EncodingDB:
    std2unicode: dict[int, str] = {}
    mac2unicode: dict[int, str] = {}
    win2unicode: dict[int, str] = {}
    pdf2unicode: dict[int, str] = {}
    for name, std, mac, win, pdf in ENCODING:
        c = name2unicode(name)
        if std:
            std2unicode[std] = c
        if mac:
            mac2unicode[mac] = c
        if win:
            win2unicode[win] = c
        if pdf:
            pdf2unicode[pdf] = c

    encodings = {
        "StandardEncoding": std2unicode,
        "MacRomanEncoding": mac2unicode,
        "WinAnsiEncoding": win2unicode,
        "PDFDocEncoding": pdf2unicode,
    }

    @classmethod
    def get_encoding(
        cls,
        name: str,
        diff: Iterable[object] | None = None,
    ) -> dict[int, str]:
        cid2unicode = cls.encodings.get(name, cls.std2unicode)
        if diff:
            cid2unicode = cid2unicode.copy()
            cid = 0
            for item in diff:
                if isinstance(item, int):
                    cid = item
                elif isinstance(item, PSLiteral):
                    try:
                        cid2unicode[cid] = name2unicode(cast(str, item.name))
                    except (KeyError, ValueError) as exc:
                        log.debug(str(exc))
                    cid += 1
        return cid2unicode


STANDARD_ENCODING_NAME = "StandardEncoding"

__all__ = [
    "EncodingDB",
    "STANDARD_ENCODING_NAME",
    "name2unicode",
]
