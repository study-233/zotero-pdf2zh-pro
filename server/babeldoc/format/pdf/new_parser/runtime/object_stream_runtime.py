from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class RuntimePdfStream:
    attrs: dict[object, object]
    rawdata: bytes
    objid: int | None = None
    genno: int | None = None

    def set_objid(self, objid: int, genno: int) -> None:
        self.objid = objid
        self.genno = genno

    def __contains__(self, name: object) -> bool:
        return name in self.attrs

    def __getitem__(self, name: str) -> object:
        return self.attrs[name]

    def get(self, name: str, default: object = None) -> object:
        return self.attrs.get(name, default)

    def get_data(self) -> bytes:
        return self.rawdata

    def get_rawdata(self) -> bytes:
        return self.rawdata
