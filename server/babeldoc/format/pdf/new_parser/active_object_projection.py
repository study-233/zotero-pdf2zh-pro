from __future__ import annotations

from collections.abc import Callable

from babeldoc.format.pdf.new_parser.active_object_backend import create_active_literal
from babeldoc.format.pdf.new_parser.active_object_backend import create_active_stream
from babeldoc.format.pdf.new_parser.active_value_access import obj_ref_id
from babeldoc.format.pdf.new_parser.object_model import PdfObjectStream

MAX_FONT_SPEC_PROJECTION_DEPTH = 128


class FontSpecProjectionError(ValueError):
    pass


def project_font_spec(
    spec: dict[object, object],
    *,
    resolve_indirect: Callable[[object], object] | None = None,
    max_depth: int = MAX_FONT_SPEC_PROJECTION_DEPTH,
) -> dict[object, object]:
    resolved = _resolve_all(
        spec,
        resolve_indirect or (lambda value: value),
        max_depth=max_depth,
    )
    projected = _project_value(resolved, max_depth=max_depth)
    if not isinstance(projected, dict):
        raise TypeError(f"Projected font spec must be dict, got {type(projected)}")
    return projected


def _check_depth(depth: int, max_depth: int) -> None:
    if depth > max_depth:
        msg = f"Font spec projection exceeded max depth {max_depth}"
        raise FontSpecProjectionError(msg)


def _resolve_all(
    value: object,
    resolve_indirect: Callable[[object], object],
    *,
    max_depth: int,
    depth: int = 0,
    active_refs: frozenset[int] = frozenset(),
    active_containers: frozenset[int] = frozenset(),
) -> object:
    _check_depth(depth, max_depth)
    ref_id = obj_ref_id(value)
    if ref_id is not None:
        if ref_id in active_refs:
            msg = f"Recursive font spec indirect reference {ref_id}"
            raise FontSpecProjectionError(msg)
        resolved = resolve_indirect(value)
        if resolved is value:
            return value
        return _resolve_all(
            resolved,
            resolve_indirect,
            max_depth=max_depth,
            depth=depth + 1,
            active_refs=active_refs | {ref_id},
            active_containers=active_containers,
        )
    if isinstance(value, list):
        container_id = id(value)
        if container_id in active_containers:
            msg = "Recursive font spec list container"
            raise FontSpecProjectionError(msg)
        return [
            _resolve_all(
                item,
                resolve_indirect,
                max_depth=max_depth,
                depth=depth + 1,
                active_refs=active_refs,
                active_containers=active_containers | {container_id},
            )
            for item in value
        ]
    if isinstance(value, dict):
        container_id = id(value)
        if container_id in active_containers:
            msg = "Recursive font spec dict container"
            raise FontSpecProjectionError(msg)
        return {
            key: _resolve_all(
                item,
                resolve_indirect,
                max_depth=max_depth,
                depth=depth + 1,
                active_refs=active_refs,
                active_containers=active_containers | {container_id},
            )
            for key, item in value.items()
        }
    if isinstance(value, PdfObjectStream):
        container_id = id(value)
        if container_id in active_containers:
            msg = "Recursive font spec stream container"
            raise FontSpecProjectionError(msg)
        return PdfObjectStream(
            attrs={
                key: _resolve_all(
                    item,
                    resolve_indirect,
                    max_depth=max_depth,
                    depth=depth + 1,
                    active_refs=active_refs,
                    active_containers=active_containers | {container_id},
                )
                for key, item in value.attrs.items()
            },
            rawdata=value.rawdata,
            objid=value.objid,
            decoded=value.decoded,
        )
    return value


def _project_value(
    value: object,
    *,
    max_depth: int,
    depth: int = 0,
    active_containers: frozenset[int] = frozenset(),
) -> object:
    _check_depth(depth, max_depth)
    if isinstance(value, str):
        if value.isascii():
            return create_active_literal(value)
        return create_active_literal(value.encode("latin-1"))
    if isinstance(value, list):
        container_id = id(value)
        if container_id in active_containers:
            msg = "Recursive projected font spec list container"
            raise FontSpecProjectionError(msg)
        return [
            _project_value(
                item,
                max_depth=max_depth,
                depth=depth + 1,
                active_containers=active_containers | {container_id},
            )
            for item in value
        ]
    if isinstance(value, dict):
        container_id = id(value)
        if container_id in active_containers:
            msg = "Recursive projected font spec dict container"
            raise FontSpecProjectionError(msg)
        return {
            key: _project_value(
                item,
                max_depth=max_depth,
                depth=depth + 1,
                active_containers=active_containers | {container_id},
            )
            for key, item in value.items()
        }
    if isinstance(value, PdfObjectStream):
        container_id = id(value)
        if container_id in active_containers:
            msg = "Recursive projected font spec stream container"
            raise FontSpecProjectionError(msg)
        attrs = {
            key: _project_value(
                item,
                max_depth=max_depth,
                depth=depth + 1,
                active_containers=active_containers | {container_id},
            )
            for key, item in _project_stream_attrs(value).items()
        }
        stream = create_active_stream(attrs, value.rawdata)
        if value.objid is not None:
            stream.set_objid(value.objid, 0)
        return stream
    return value


def _project_stream_attrs(value: PdfObjectStream) -> dict[object, object]:
    if not value.decoded:
        return value.attrs

    return {
        key: item
        for key, item in value.attrs.items()
        if key not in {"Filter", "DecodeParms", "F", "FDecodeParms", "DL", "Length"}
    }
