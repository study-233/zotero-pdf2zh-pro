from __future__ import annotations

from collections.abc import Iterable
from typing import cast

from babeldoc.format.pdf.new_parser.runtime.object_primitives_runtime import resolve1


def _choplist(size: int, values: list[object]):
    for index in range(0, len(values), size):
        chunk = values[index : index + size]
        if len(chunk) == size:
            yield tuple(chunk)


def get_widths(seq: Iterable[object]) -> dict[str | int, float]:
    widths: dict[int, float] = {}
    range_spec: list[float] = []
    for value in seq:
        value = resolve1(value)
        if isinstance(value, list):
            if range_spec:
                char1 = range_spec[-1]
                for i, width in enumerate(value):
                    widths[cast(int, char1) + i] = width
                range_spec = []
        elif isinstance(value, int | float):
            range_spec.append(value)
            if len(range_spec) == 3:
                char1, char2, width = range_spec
                if isinstance(char1, int) and isinstance(char2, int):
                    for i in range(cast(int, char1), cast(int, char2) + 1):
                        widths[i] = width
                range_spec = []
    return cast(dict[str | int, float], widths)


def get_widths2(seq: Iterable[object]) -> dict[int, tuple[float, tuple[float, float]]]:
    widths: dict[int, tuple[float, tuple[float, float]]] = {}
    range_spec: list[float] = []
    for value in seq:
        if isinstance(value, list):
            if range_spec:
                char1 = range_spec[-1]
                for i, (width, vx, vy) in enumerate(_choplist(3, value)):
                    widths[cast(int, char1) + i] = (width, (vx, vy))
                range_spec = []
        elif isinstance(value, int | float):
            range_spec.append(value)
            if len(range_spec) == 5:
                char1, char2, width, vx, vy = range_spec
                for i in range(cast(int, char1), cast(int, char2) + 1):
                    widths[i] = (width, (vx, vy))
                range_spec = []
    return widths
