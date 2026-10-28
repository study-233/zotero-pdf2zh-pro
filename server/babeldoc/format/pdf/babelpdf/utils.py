from __future__ import annotations

from typing import Any


def _resolve_indirect_bbox_value(value: Any) -> Any:
    """Resolve one pdfminer-style indirect object without importing pdfminer."""
    resolve = getattr(value, "resolve", None)
    if callable(resolve) and hasattr(value, "objid"):
        return resolve()
    return value


def guarded_bbox(bbox):
    bbox_guarded = []
    for v in bbox:
        u = _resolve_indirect_bbox_value(v)
        if isinstance(u, int) or isinstance(u, float):
            bbox_guarded.append(u)
        else:
            bbox_guarded.append(u)
    return bbox_guarded
