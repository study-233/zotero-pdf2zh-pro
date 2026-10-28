from __future__ import annotations

import logging
from typing import Any

from babeldoc.format.pdf.new_parser.runtime.cmap_secure_loader import CMapIntegrityError
from babeldoc.format.pdf.new_parser.runtime.cmap_secure_loader import (
    load_verified_cmap_data,
)
from babeldoc.format.pdf.new_parser.runtime.cmap_types_runtime import CMap
from babeldoc.format.pdf.new_parser.runtime.cmap_types_runtime import CMapBase
from babeldoc.format.pdf.new_parser.runtime.cmap_types_runtime import IdentityCMap
from babeldoc.format.pdf.new_parser.runtime.cmap_types_runtime import IdentityCMapByte
from babeldoc.format.pdf.new_parser.runtime.cmap_types_runtime import UnicodeMap

log = logging.getLogger(__name__)


class CMapError(Exception):
    pass


class PyCMap(CMap):
    def __init__(self, name: str, module: Any) -> None:
        super().__init__(CMapName=name)
        self.code2cid = module.CODE2CID
        if module.IS_VERTICAL:
            self.attrs["WMode"] = 1


class PyUnicodeMap(UnicodeMap):
    def __init__(self, name: str, module: Any, vertical: bool) -> None:
        super().__init__(CMapName=name)
        if vertical:
            self.cid2unichr = module.CID2UNICHR_V
            self.attrs["WMode"] = 1
        else:
            self.cid2unichr = module.CID2UNICHR_H


class CMapDB:
    _cmap_cache: dict[str, PyCMap] = {}
    _umap_cache: dict[str, list[PyUnicodeMap]] = {}

    class CMapNotFound(CMapError):  # noqa: N818 - preserve pdfminer-compatible API.
        pass

    @classmethod
    def _load_data(cls, name: str) -> Any:
        clean_name = name.replace("\0", "")
        log.debug("loading: %r", clean_name)
        try:
            data = load_verified_cmap_data(clean_name)
        except CMapIntegrityError as exc:
            raise CMapDB.CMapNotFound(clean_name) from exc
        return type(str(clean_name), (), data)

    @classmethod
    def get_cmap(cls, name: str) -> CMapBase:
        if name == "Identity-H":
            return IdentityCMap(WMode=0)
        if name == "Identity-V":
            return IdentityCMap(WMode=1)
        if name == "OneByteIdentityH":
            return IdentityCMapByte(WMode=0)
        if name == "OneByteIdentityV":
            return IdentityCMapByte(WMode=1)
        try:
            return cls._cmap_cache[name]
        except KeyError:
            pass
        data = cls._load_data(name)
        cls._cmap_cache[name] = cmap = PyCMap(name, data)
        return cmap

    @classmethod
    def get_unicode_map(cls, name: str, vertical: bool = False) -> UnicodeMap:
        try:
            return cls._umap_cache[name][vertical]
        except KeyError:
            pass
        data = cls._load_data(f"to-unicode-{name}")
        cls._umap_cache[name] = [
            PyUnicodeMap(name, data, value) for value in (False, True)
        ]
        return cls._umap_cache[name][vertical]
