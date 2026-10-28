from __future__ import annotations

from dataclasses import dataclass


class PdfObjectDict(dict):
    def __init__(self, *args, objid: int | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.objid = objid


@dataclass(frozen=True, slots=True)
class ActiveLiteral:
    name: str | bytes


@dataclass(frozen=True, slots=True)
class PdfIndirectRef:
    objid: int
    generation: int = 0


@dataclass(frozen=True)
class PdfObjectStream:
    attrs: dict[str, object]
    rawdata: bytes
    objid: int | None = None
    decoded: bool = False

    def get(self, key: str, default: object = None) -> object:
        return self.attrs.get(key, default)

    def __contains__(self, key: object) -> bool:
        return key in self.attrs

    def get_data(self) -> bytes:
        return self.rawdata
