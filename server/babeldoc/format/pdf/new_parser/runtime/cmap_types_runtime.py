from __future__ import annotations

import logging
import struct
import sys
from collections.abc import Iterable
from collections.abc import Iterator
from collections.abc import MutableMapping
from typing import TextIO
from typing import cast

from babeldoc.format.pdf.new_parser.runtime.exceptions_runtime import PDFTypeError
from babeldoc.format.pdf.new_parser.runtime.font_encoding_runtime import name2unicode
from babeldoc.format.pdf.new_parser.runtime.ps_primitives_runtime import PSLiteral

log = logging.getLogger(__name__)


class CMapBase:
    debug = 0

    def __init__(self, **kwargs: object) -> None:
        self.attrs: MutableMapping[str, object] = kwargs.copy()

    def is_vertical(self) -> bool:
        return self.attrs.get("WMode", 0) != 0

    def set_attr(self, key: str, value: object) -> None:
        self.attrs[key] = value

    def add_code2cid(self, code: str, cid: int) -> None:
        _ = (code, cid)

    def add_cid2unichr(self, cid: int, code: PSLiteral | bytes | int) -> None:
        _ = (cid, code)

    def use_cmap(self, cmap: CMapBase) -> None:
        _ = cmap

    def decode(self, code: bytes) -> Iterable[int]:
        raise NotImplementedError


class CMap(CMapBase):
    def __init__(self, **kwargs: str | int) -> None:
        super().__init__(**kwargs)
        self.code2cid: dict[int, object] = {}

    def __repr__(self) -> str:
        return f"<CMap: {self.attrs.get('CMapName')}>"

    def use_cmap(self, cmap: CMapBase) -> None:
        assert isinstance(cmap, CMap), str(type(cmap))

        def copy(dst: dict[int, object], src: dict[int, object]) -> None:
            for key, value in src.items():
                if isinstance(value, dict):
                    nested: dict[int, object] = {}
                    dst[key] = nested
                    copy(nested, value)
                else:
                    dst[key] = value

        copy(self.code2cid, cmap.code2cid)

    def decode(self, code: bytes) -> Iterator[int]:
        log.debug("decode: %r, %r", self, code)
        current = self.code2cid
        for byte in iter(code):
            if byte in current:
                value = current[byte]
                if isinstance(value, int):
                    yield value
                    current = self.code2cid
                else:
                    current = cast(dict[int, object], value)
            else:
                current = self.code2cid

    def dump(
        self,
        out: TextIO = sys.stdout,
        code2cid: dict[int, object] | None = None,
        code: tuple[int, ...] = (),
    ) -> None:
        if code2cid is None:
            code2cid = self.code2cid
            code = ()
        for key, value in sorted(code2cid.items()):
            next_code = code + (key,)
            if isinstance(value, int):
                out.write(f"code {next_code!r} = cid {value}\n")
            else:
                self.dump(
                    out=out,
                    code2cid=cast(dict[int, object], value),
                    code=next_code,
                )


class IdentityCMap(CMapBase):
    def decode(self, code: bytes) -> tuple[int, ...]:
        length = len(code) // 2
        if length:
            return cast(tuple[int, ...], struct.unpack_from(f">{length}H", code))
        return ()


class IdentityCMapByte(IdentityCMap):
    def decode(self, code: bytes) -> tuple[int, ...]:
        length = len(code)
        if length:
            return cast(tuple[int, ...], struct.unpack(f">{length}B", code))
        return ()


class UnicodeMap(CMapBase):
    def __init__(self, **kwargs: str | int) -> None:
        super().__init__(**kwargs)
        self.cid2unichr: dict[int, str] = {}

    def __repr__(self) -> str:
        return f"<UnicodeMap: {self.attrs.get('CMapName')}>"

    def get_unichr(self, cid: int) -> str:
        return self.cid2unichr[cid]

    def dump(self, out: TextIO = sys.stdout) -> None:
        for key, value in sorted(self.cid2unichr.items()):
            out.write(f"cid {key} = unicode {value!r}\n")


class IdentityUnicodeMap(UnicodeMap):
    def get_unichr(self, cid: int) -> str:
        return chr(cid)


class FileUnicodeMap(UnicodeMap):
    def add_cid2unichr(self, cid: int, code: object) -> None:
        assert isinstance(cid, int), str(type(cid))
        if hasattr(code, "name"):
            name = code.name
            if isinstance(name, bytes):
                name = name.decode("utf-8", "ignore")
            unichr = name2unicode(name)
        elif isinstance(code, bytes):
            unichr = code.decode("UTF-16BE", "ignore")
        elif isinstance(code, int):
            unichr = chr(code)
        else:
            raise PDFTypeError(code)

        if unichr == "\u00a0" and self.cid2unichr.get(cid) == " ":
            return
        self.cid2unichr[cid] = unichr
