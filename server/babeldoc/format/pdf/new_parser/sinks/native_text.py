from __future__ import annotations

from typing import Protocol

from babeldoc.format.pdf.new_parser.interpreter import BeginXObjectEvent
from babeldoc.format.pdf.new_parser.interpreter import ClipPathEvent
from babeldoc.format.pdf.new_parser.interpreter import EndXObjectEvent
from babeldoc.format.pdf.new_parser.interpreter import GraphicStateOpEvent
from babeldoc.format.pdf.new_parser.interpreter import ImageXObjectEvent
from babeldoc.format.pdf.new_parser.interpreter import InlineImageEvent
from babeldoc.format.pdf.new_parser.interpreter import PathPaintEvent
from babeldoc.format.pdf.new_parser.interpreter import RestoreGraphicsStateEvent
from babeldoc.format.pdf.new_parser.interpreter import SaveGraphicsStateEvent
from babeldoc.format.pdf.new_parser.interpreter import ShadingPaintEvent
from babeldoc.format.pdf.new_parser.interpreter import TextRunEvent
from babeldoc.format.pdf.new_parser.resources import PageResourceBundle
from babeldoc.format.pdf.new_parser.text_positioning import (
    DEFAULT_NATIVE_TEXT_RUN_POSITIONER,
)
from babeldoc.format.pdf.new_parser.text_positioning import TextRunPositioner

MAX_BUFFERED_NATIVE_ITEMS = 2048


class _ProjectionSink(Protocol):
    def begin_native_root_scope(
        self,
        resource_bundle: PageResourceBundle,
        *,
        emitted_font_keys: set[object],
    ) -> None: ...

    def begin_native_xobject_scope(
        self,
        event: BeginXObjectEvent,
        resource_bundle: PageResourceBundle,
        *,
        parent_xobj_id: int,
        emitted_font_keys: set[object],
    ) -> tuple[str, int | None]: ...

    def end_native_xobject(self, xobj_id: int, base_op: str = " ") -> None: ...

    def push_native_graphics_state(self, event) -> None: ...

    def pop_native_graphics_state(self, event) -> None: ...

    def apply_native_graphic_state_op(self, event) -> None: ...

    def emit_native_clip_path(self, event) -> None: ...

    def buffer_native_text_run(
        self,
        event: TextRunEvent,
        resource_bundle: PageResourceBundle,
        *,
        xobj_id: int,
        text_run_positioner: TextRunPositioner,
    ) -> list[object]: ...

    def buffer_native_path_paint(
        self,
        event: PathPaintEvent,
        *,
        xobj_id: int,
    ) -> list[object]: ...

    def buffer_native_shading_paint(
        self,
        event: ShadingPaintEvent,
        *,
        xobj_id: int,
    ) -> list[object]: ...

    def flush_native_buffered_items(self, items: list[object]) -> int: ...

    def emit_native_inline_image(
        self, event: InlineImageEvent, *, xobj_id: int
    ) -> None: ...

    def emit_native_image_xobject(
        self, event: ImageXObjectEvent, *, xobj_id: int
    ) -> None: ...


class _NativeLegacyProjectionSession:
    def __init__(
        self,
        events: list[object],
        resource_bundle: PageResourceBundle,
        sink: _ProjectionSink,
        *,
        root_xobj_id: int,
        xobject_end_operations: dict[tuple[str, ...], str] | None,
        text_run_positioner: TextRunPositioner,
    ) -> None:
        self.events = events
        self.resource_bundle = resource_bundle
        self.sink = sink
        self.root_xobj_id = root_xobj_id
        self.xobject_end_operations = xobject_end_operations or {}
        self.emitted_font_keys: set[object] = set()
        self.text_run_positioner = text_run_positioner

    def emit(self) -> int:
        self.sink.begin_native_root_scope(
            self.resource_bundle,
            emitted_font_keys=self.emitted_font_keys,
        )
        emitted, _ = self._emit_scope(
            xobj_id=self.root_xobj_id,
            start_index=0,
            stop_on_end=False,
        )
        return emitted

    def _emit_scope(
        self,
        *,
        xobj_id: int,
        start_index: int,
        stop_on_end: bool,
    ) -> tuple[int, int]:
        emitted = 0
        index = start_index
        buffered_items: list[object] = []

        def flush_if_needed() -> None:
            nonlocal emitted
            if len(buffered_items) < MAX_BUFFERED_NATIVE_ITEMS:
                return
            emitted += self.sink.flush_native_buffered_items(buffered_items)
            buffered_items.clear()

        while index < len(self.events):
            event = self.events[index]
            if isinstance(event, EndXObjectEvent):
                if stop_on_end:
                    emitted += self.sink.flush_native_buffered_items(buffered_items)
                    return emitted, index + 1
                index += 1
                continue
            if isinstance(event, TextRunEvent):
                buffered_items.extend(
                    self.sink.buffer_native_text_run(
                        event,
                        self.resource_bundle,
                        xobj_id=xobj_id,
                        text_run_positioner=self.text_run_positioner,
                    )
                )
                flush_if_needed()
                index += 1
                continue
            if isinstance(event, SaveGraphicsStateEvent):
                self.sink.push_native_graphics_state(event)
                index += 1
                continue
            if isinstance(event, RestoreGraphicsStateEvent):
                self.sink.pop_native_graphics_state(event)
                index += 1
                continue
            if isinstance(event, GraphicStateOpEvent):
                self.sink.apply_native_graphic_state_op(event)
                index += 1
                continue
            if isinstance(event, ClipPathEvent):
                self.sink.emit_native_clip_path(event)
                index += 1
                continue
            if isinstance(event, PathPaintEvent):
                buffered_items.extend(
                    self.sink.buffer_native_path_paint(event, xobj_id=xobj_id)
                )
                flush_if_needed()
                index += 1
                continue
            if isinstance(event, ShadingPaintEvent):
                buffered_items.extend(
                    self.sink.buffer_native_shading_paint(event, xobj_id=xobj_id)
                )
                flush_if_needed()
                index += 1
                continue
            if isinstance(event, InlineImageEvent):
                self.sink.emit_native_inline_image(event, xobj_id=xobj_id)
                emitted += 1
                index += 1
                continue
            if isinstance(event, ImageXObjectEvent):
                self.sink.emit_native_image_xobject(event, xobj_id=xobj_id)
                emitted += 1
                index += 1
                continue
            if isinstance(event, BeginXObjectEvent):
                handled, emitted_delta, next_index = self._emit_begin_xobject(
                    event,
                    parent_xobj_id=xobj_id,
                    start_index=index,
                )
                if handled:
                    emitted += emitted_delta
                    index = next_index
                    continue
            index += 1
        emitted += self.sink.flush_native_buffered_items(buffered_items)
        return emitted, index

    def _emit_begin_xobject(
        self,
        event: BeginXObjectEvent,
        *,
        parent_xobj_id: int,
        start_index: int,
    ) -> tuple[bool, int, int]:
        kind, child_xobj_id = self.sink.begin_native_xobject_scope(
            event,
            self.resource_bundle,
            parent_xobj_id=parent_xobj_id,
            emitted_font_keys=self.emitted_font_keys,
        )
        if kind == "form" and child_xobj_id is not None:
            child_emitted, next_index = self._emit_scope(
                xobj_id=child_xobj_id,
                start_index=start_index + 1,
                stop_on_end=True,
            )
            self.sink.end_native_xobject(
                child_xobj_id,
                self.xobject_end_operations.get((*event.xobject_path, event.name), " "),
            )
            return True, 1 + child_emitted, next_index
        if kind == "image":
            next_index = (
                start_index + 2
                if start_index + 1 < len(self.events)
                else start_index + 1
            )
            return True, 1, next_index
        return False, 0, start_index


def emit_native_text_events_to_legacy_sink(
    events: list[object],
    resource_bundle: PageResourceBundle,
    sink: _ProjectionSink,
    *,
    xobj_id: int = 0,
    xobject_end_operations: dict[tuple[str, ...], str] | None = None,
    text_run_positioner: TextRunPositioner = DEFAULT_NATIVE_TEXT_RUN_POSITIONER,
) -> int:
    return _NativeLegacyProjectionSession(
        events,
        resource_bundle,
        sink,
        root_xobj_id=xobj_id,
        xobject_end_operations=xobject_end_operations,
        text_run_positioner=text_run_positioner,
    ).emit()
