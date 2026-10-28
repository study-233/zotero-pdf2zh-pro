from __future__ import annotations

from typing import Any
from typing import Generic
from typing import TypeVar

from babeldoc.format.pdf.new_parser.runtime.exceptions_runtime import PSTypeError
from babeldoc.format.pdf.new_parser.runtime.runtime_settings import STRICT


class PSObject:
    """Base class for PostScript-related data types used by parser-owned runtime."""


class PSLiteral(PSObject):
    NameType = str | bytes

    def __init__(self, name: NameType) -> None:
        self.name = name

    def __repr__(self) -> str:
        return f"/{self.name!r}"


class PSKeyword(PSObject):
    def __init__(self, name: bytes) -> None:
        self.name = name

    def __repr__(self) -> str:
        return f"/{self.name!r}"


_SymbolT = TypeVar("_SymbolT", PSLiteral, PSKeyword)


class PSSymbolTable(Generic[_SymbolT]):
    def __init__(self, klass: type[_SymbolT]) -> None:
        self.dict: dict[PSLiteral.NameType, _SymbolT] = {}
        self.klass: type[_SymbolT] = klass

    def intern(self, name: PSLiteral.NameType) -> _SymbolT:
        if name in self.dict:
            return self.dict[name]
        lit = self.klass(name)  # type: ignore[arg-type]
        self.dict[name] = lit
        return lit


PSLiteralTable = PSSymbolTable(PSLiteral)
PSKeywordTable = PSSymbolTable(PSKeyword)
LIT = PSLiteralTable.intern
KWD = PSKeywordTable.intern


def literal_name(value: Any) -> str:
    if isinstance(value, PSLiteral):
        if isinstance(value.name, str):
            return value.name
        try:
            return str(value.name, "utf-8")
        except UnicodeDecodeError:
            return str(value.name)
    if STRICT:
        raise PSTypeError(f"Literal required: {value!r}")
    return str(value)
