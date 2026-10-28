from __future__ import annotations

from collections.abc import Mapping

from babeldoc.format.pdf.new_parser.runtime.cmap_types_runtime import CMap
from babeldoc.format.pdf.new_parser.runtime.cmap_types_runtime import FileUnicodeMap
from babeldoc.format.pdf.new_parser.runtime.cmap_types_runtime import IdentityCMap
from babeldoc.format.pdf.new_parser.runtime.cmap_types_runtime import IdentityCMapByte
from babeldoc.format.pdf.new_parser.runtime.cmap_types_runtime import IdentityUnicodeMap
from babeldoc.format.pdf.new_parser.runtime.cmapdb_runtime import CMapDB
from babeldoc.format.pdf.new_parser.runtime.runtime_settings import STRICT
from babeldoc.format.pdf.new_parser.runtime.to_unicode_parser_runtime import (
    parse_tounicode_stream,
)


def _normalize_cmap_name(cmap_name: object) -> str:
    if isinstance(cmap_name, bytes):
        cmap_name = cmap_name.decode("latin-1", "ignore")
    else:
        cmap_name = str(cmap_name)
    if cmap_name.startswith("/"):
        cmap_name = cmap_name[1:]
    if (
        len(cmap_name) >= 2
        and cmap_name[0] == cmap_name[-1]
        and cmap_name[0]
        in (
            "'",
            '"',
        )
    ):
        cmap_name = cmap_name[1:-1]
    return cmap_name


def _build_identity_cmap(cmap_name: str):
    if cmap_name == "Identity-H":
        return IdentityCMap(WMode=0)
    if cmap_name == "Identity-V":
        return IdentityCMap(WMode=1)
    if cmap_name == "OneByteIdentityH":
        return IdentityCMapByte(WMode=0)
    if cmap_name == "OneByteIdentityV":
        return IdentityCMapByte(WMode=1)
    return None


def build_cid_cmap(spec: Mapping[object, object], *, literal_name):
    cmap_name = "unknown"
    try:
        spec_encoding = spec["Encoding"]
        if hasattr(spec_encoding, "name"):
            cmap_name = literal_name(spec["Encoding"])
        else:
            cmap_name = literal_name(spec_encoding["CMapName"])
    except KeyError:
        if STRICT:
            raise
    if hasattr(cmap_name, "get"):
        cmap_name_stream = cmap_name
        if "CMapName" in cmap_name_stream:
            cmap_name = cmap_name_stream.get("CMapName").name
        elif STRICT:
            raise KeyError("CMapName unspecified for encoding")
    cmap_name = _normalize_cmap_name(cmap_name)
    cmap_name = {
        "DLIdent-H": "Identity-H",
        "DLIdent-V": "Identity-V",
        "OneByteIdentityH": "OneByteIdentityH",
        "OneByteIdentityV": "OneByteIdentityV",
    }.get(cmap_name, cmap_name)
    identity_cmap = _build_identity_cmap(cmap_name)
    if identity_cmap is not None:
        return cmap_name, identity_cmap
    try:
        return cmap_name, CMapDB.get_cmap(cmap_name)
    except Exception:
        if STRICT:
            raise
        return cmap_name, CMap()


def build_cid_unicode_map(
    spec: Mapping[object, object],
    *,
    cid_ordering: str,
    ttf: object | None,
    cmap: object,
    stream_value,
    literal_name,
):
    unicode_map = None
    if "ToUnicode" in spec:
        try:
            stream = stream_value(spec["ToUnicode"])
        except Exception:
            stream = None
        if stream is not None and hasattr(stream, "get_data"):
            unicode_map = FileUnicodeMap()
            parse_tounicode_stream(stream.get_data(), unicode_map)
        else:
            try:
                cmap_name = literal_name(spec["ToUnicode"])
                encoding = literal_name(spec["Encoding"])
                if (
                    "Identity" in cid_ordering
                    or "Identity" in cmap_name
                    or "Identity" in encoding
                ):
                    unicode_map = IdentityUnicodeMap()
            except Exception:
                pass
    elif cid_ordering in ("Adobe-Identity", "Adobe-UCS"):
        if ttf is not None:
            try:
                unicode_map = ttf.create_unicode_map()
            except ttf.CMapNotFound:
                pass
    else:
        try:
            unicode_map = CMapDB.get_unicode_map(cid_ordering, cmap.is_vertical())
        except CMapDB.CMapNotFound:
            pass
    return unicode_map
