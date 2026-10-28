from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from babeldoc.format.pdf.new_parser.active_value_access import dict_value
from babeldoc.format.pdf.new_parser.active_value_access import list_value
from babeldoc.format.pdf.new_parser.active_value_access import literal_name
from babeldoc.format.pdf.new_parser.active_value_access import stream_value
from babeldoc.format.pdf.new_parser.prepared_page import PreparedFontSpec
from babeldoc.format.pdf.new_parser.prepared_page import PreparedXObject


@dataclass(frozen=True, slots=True)
class PreparedObjectAccess:
    dict_value: Callable[[object], object]
    list_value: Callable[[object], object]
    stream_value: Callable[[object], object]
    literal_name: Callable[[object], str]
    resolve_indirect: Callable[[object], object]


DEFAULT_OBJECT_ACCESS = PreparedObjectAccess(
    dict_value=dict_value,
    list_value=list_value,
    stream_value=stream_value,
    literal_name=literal_name,
    resolve_indirect=lambda value: value,
)


def build_prepared_xobject_map(
    resources: dict[object, object] | None,
    *,
    prepared_cache: dict[int, PreparedXObject] | None = None,
    active_keys: set[int] | None = None,
    resource_map_cache: dict[int, dict[str, PreparedXObject]] | None = None,
    object_access: PreparedObjectAccess = DEFAULT_OBJECT_ACCESS,
) -> dict[str, PreparedXObject]:
    if prepared_cache is None:
        prepared_cache = {}
    if active_keys is None:
        active_keys = set()
    if resource_map_cache is None:
        resource_map_cache = {}
    if not resources:
        return {}

    resolved_resources = object_access.dict_value(resources)
    resource_key = id(resolved_resources)
    cached_map = resource_map_cache.get(resource_key)
    if cached_map is not None:
        return cached_map

    result: dict[str, PreparedXObject] = {}
    resource_map_cache[resource_key] = result
    xobjects = object_access.dict_value(resolved_resources.get("XObject", {}))
    for xobj_id, xobj_stream in xobjects.items():
        try:
            prepared_xobj = _resolve_preparable_xobject(
                xobj_stream,
                object_access=object_access,
            )
        except TypeError:
            continue
        result[object_access.literal_name(xobj_id)] = prepare_xobject_details(
            prepared_xobj,
            fallback_resources=resources,
            prepared_cache=prepared_cache,
            active_keys=active_keys,
            resource_map_cache=resource_map_cache,
            object_access=object_access,
        )
    return result


def build_prepared_font_specs(
    resources: dict[object, object] | None,
    *,
    object_access: PreparedObjectAccess = DEFAULT_OBJECT_ACCESS,
) -> tuple[PreparedFontSpec, ...]:
    if not resources:
        return ()
    font_section = object_access.dict_value(
        object_access.dict_value(resources).get("Font", {})
    )
    result: list[PreparedFontSpec] = []
    for font_id, spec in font_section.items():
        try:
            spec_dict = object_access.dict_value(spec)
        except TypeError:
            continue
        result.append(
            PreparedFontSpec(
                name=object_access.literal_name(font_id),
                objid=getattr(spec, "objid", None),
                spec=spec_dict,
                resolve_indirect=object_access.resolve_indirect,
            )
        )
    return tuple(result)


def _xobject_cache_key(xobj: object) -> int:
    objid = getattr(xobj, "objid", None)
    if objid is not None:
        return int(objid)
    return id(xobj)


def _resolve_preparable_xobject(
    xobj: object,
    *,
    object_access: PreparedObjectAccess,
) -> object:
    try:
        return object_access.stream_value(xobj)
    except TypeError:
        return object_access.dict_value(xobj)


def prepare_xobject_details(
    xobj: object,
    *,
    fallback_resources: dict[object, object] | None,
    prepared_cache: dict[int, PreparedXObject] | None = None,
    active_keys: set[int] | None = None,
    resource_map_cache: dict[int, dict[str, PreparedXObject]] | None = None,
    object_access: PreparedObjectAccess = DEFAULT_OBJECT_ACCESS,
) -> PreparedXObject:
    if prepared_cache is None:
        prepared_cache = {}
    if active_keys is None:
        active_keys = set()
    if resource_map_cache is None:
        resource_map_cache = {}
    cache_key = _xobject_cache_key(xobj)
    cached = prepared_cache.get(cache_key)
    if cached is not None:
        return cached

    subtype = xobj.get("Subtype")
    subtype_name = object_access.literal_name(subtype) if subtype is not None else None
    if subtype_name != "Form" or "BBox" not in xobj:
        prepared = PreparedXObject(
            subtype_name=subtype_name,
            xref_id=getattr(xobj, "objid", None),
            font_specs=build_prepared_font_specs(
                fallback_resources,
                object_access=object_access,
            ),
            xobject_map={},
            bbox=(0.0, 0.0, 0.0, 0.0),
            matrix=(1.0, 0.0, 0.0, 1.0, 0.0, 0.0),
            data=_xobject_data(xobj),
        )
        prepared_cache[cache_key] = prepared
        return prepared

    xobj_resources = xobj.get("Resources")
    resources = (
        object_access.dict_value(xobj_resources)
        if xobj_resources
        else fallback_resources
    )
    shared_xobject_map = (
        resource_map_cache.get(id(resources)) if resources is not None else None
    )
    bbox_values = object_access.list_value(xobj.get("BBox"))
    bbox = tuple(float(value) for value in bbox_values[:4])
    matrix_values = object_access.list_value(xobj.get("Matrix", (1, 0, 0, 1, 0, 0)))
    matrix = tuple(float(value) for value in matrix_values)
    if cache_key in active_keys:
        prepared = PreparedXObject(
            subtype_name=subtype_name,
            xref_id=getattr(xobj, "objid", None),
            font_specs=build_prepared_font_specs(
                resources,
                object_access=object_access,
            ),
            xobject_map={},
            bbox=bbox,
            matrix=matrix,
            data=_xobject_data(xobj),
        )
        prepared_cache[cache_key] = prepared
        return prepared

    prepared_children: dict[str, PreparedXObject] = (
        shared_xobject_map if shared_xobject_map is not None else {}
    )
    prepared = PreparedXObject(
        subtype_name=subtype_name,
        xref_id=getattr(xobj, "objid", None),
        font_specs=build_prepared_font_specs(
            resources,
            object_access=object_access,
        ),
        xobject_map=prepared_children,
        bbox=bbox,
        matrix=matrix,
        data=_xobject_data(xobj),
    )
    prepared_cache[cache_key] = prepared

    active_keys.add(cache_key)
    try:
        child_map = build_prepared_xobject_map(
            resources,
            prepared_cache=prepared_cache,
            active_keys=active_keys,
            resource_map_cache=resource_map_cache,
            object_access=object_access,
        )
        if child_map is not prepared_children:
            prepared_children.update(child_map)
    finally:
        active_keys.discard(cache_key)
    return prepared


def _xobject_data(xobj: object) -> bytes:
    get_data = getattr(xobj, "get_data", None)
    if callable(get_data):
        return get_data()
    return b""
