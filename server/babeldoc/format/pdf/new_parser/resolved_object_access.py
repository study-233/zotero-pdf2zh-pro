from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from babeldoc.format.pdf.new_parser.active_value_access import literal_name
from babeldoc.format.pdf.new_parser.active_value_access import obj_ref_id
from babeldoc.format.pdf.new_parser.object_model import PdfObjectDict
from babeldoc.format.pdf.new_parser.object_model import PdfObjectStream
from babeldoc.format.pdf.new_parser.prepared_resource_builder import (
    PreparedObjectAccess,
)


@dataclass(frozen=True, slots=True)
class ResolvedObjectAccess:
    object_store: dict[int, object]
    resolver: Callable[[int], object] | None = None

    def resolve(self, value: object) -> object:
        ref_id = obj_ref_id(value)
        if ref_id is not None:
            cached = self.object_store.get(ref_id)
            if cached is not None:
                return cached
            if self.resolver is not None:
                resolved = self.resolver(ref_id)
                self.object_store[ref_id] = resolved
                return resolved
            raise KeyError(ref_id)
        return value

    def dict_value(self, value: object):
        resolved = self.resolve(value)
        if isinstance(resolved, dict):
            return resolved
        raise TypeError(f"Expected dict-like object, got {type(resolved)}")

    def list_value(self, value: object):
        resolved = self.resolve(value)
        if isinstance(resolved, list | tuple):
            return list(resolved)
        raise TypeError(f"Expected list-like object, got {type(resolved)}")

    def stream_value(self, value: object):
        resolved = self.resolve(value)
        if isinstance(resolved, PdfObjectStream):
            return resolved
        raise TypeError(f"Expected stream-like object, got {type(resolved)}")

    def literal_name(self, value: object):
        return literal_name(value)

    def as_prepared_object_access(self) -> PreparedObjectAccess:
        return PreparedObjectAccess(
            dict_value=self.dict_value,
            list_value=self.list_value,
            stream_value=self.stream_value,
            literal_name=self.literal_name,
            resolve_indirect=self.resolve,
        )


def object_dict(value: dict[str, object], *, objid: int | None = None) -> PdfObjectDict:
    return PdfObjectDict(value, objid=objid)
