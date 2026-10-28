from __future__ import annotations

import base64
import functools
import logging
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from io import BytesIO
from itertools import islice

import freetype

from babeldoc.format.pdf.babelpdf.cidfont import get_glyph_bbox
from babeldoc.format.pdf.babelpdf.encoding import WinAnsiEncoding
from babeldoc.format.pdf.babelpdf.encoding import get_type1_encoding


def batched(iterable, n, *, strict=False):
    if n < 1:
        raise ValueError("n must be at least one")
    iterator = iter(iterable)
    while batch := tuple(islice(iterator, n)):
        if strict and len(batch) != n:
            raise ValueError("batched(): incomplete batch")
        yield batch


logger = logging.getLogger(__name__)

PassthroughInstruction = tuple[str, object]


@dataclass(frozen=True, slots=True)
class CtmAwarePassthroughArg:
    value: str
    ctm: tuple[float, float, float, float, float, float]

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class _PassthroughNode:
    parent: _PassthroughNode | None
    instruction: PassthroughInstruction
    replace_key: str | None
    version: int
    length: int


class PassthroughSnapshot:
    __slots__ = ("tail", "latest_versions", "next_version", "_render_cache")

    def __init__(
        self,
        tail: _PassthroughNode | None = None,
        latest_versions: tuple[tuple[str, int], ...] = (),
        next_version: int = 1,
    ) -> None:
        self.tail = tail
        self.latest_versions = latest_versions
        self.next_version = next_version
        self._render_cache: dict[bool, str] = {}

    def __bool__(self) -> bool:
        return self.tail is not None

    def __iter__(self):
        return iter(self.to_tuple())

    def __len__(self) -> int:
        return len(self.to_tuple())

    @property
    def event_count(self) -> int:
        return self.tail.length if self.tail is not None else 0

    def to_tuple(self) -> tuple[PassthroughInstruction, ...]:
        latest = dict(self.latest_versions)
        kept: list[PassthroughInstruction] = []
        node = self.tail
        while node is not None:
            if node.replace_key is None or latest.get(node.replace_key) == node.version:
                kept.append(node.instruction)
            node = node.parent
        kept.reverse()
        return tuple(kept)

    def render(self, *, include_clipping: bool = False) -> str:
        if include_clipping in self._render_cache:
            return self._render_cache[include_clipping]
        rendered = _render_passthrough_instructions(
            self.to_tuple(),
            include_clipping=include_clipping,
        )
        self._render_cache[include_clipping] = rendered
        return rendered


class LazyPassthroughInstruction:
    """String-compatible wrapper that defers expensive graphic-state rendering."""

    __slots__ = ("snapshot", "include_clipping", "suffix_parts", "_value")

    def __init__(
        self,
        snapshot: PassthroughSnapshot,
        *,
        include_clipping: bool = False,
        suffix_parts: Iterable[str] = (),
    ) -> None:
        self.snapshot = snapshot
        self.include_clipping = include_clipping
        self.suffix_parts = tuple(part for part in suffix_parts if part)
        self._value: str | None = None

    def materialize(self) -> str:
        if self._value is None:
            parts = []
            base = render_passthrough_snapshot(
                self.snapshot,
                include_clipping=self.include_clipping,
            )
            if base:
                parts.append(base)
            parts.extend(self.suffix_parts)
            self._value = " ".join(parts)
        return self._value

    def encode(self, *args, **kwargs):
        return self.materialize().encode(*args, **kwargs)

    def __bool__(self) -> bool:
        return bool(self.snapshot) or bool(self.suffix_parts)

    def __str__(self) -> str:
        return self.materialize()

    def __repr__(self) -> str:
        return repr(self.materialize())

    def __eq__(self, other) -> bool:
        if isinstance(other, LazyPassthroughInstruction):
            return self.materialize() == other.materialize()
        if isinstance(other, str):
            return self.materialize() == other
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.materialize())


def _latest_without_operator(
    latest_versions: tuple[tuple[str, int], ...],
    operator: str,
) -> tuple[tuple[str, int], ...]:
    return tuple((op, version) for op, version in latest_versions if op != operator)


def _is_snapshot(value) -> bool:
    return isinstance(value, PassthroughSnapshot)


ClipPathSegment = tuple[object, ...]
ClipPathInstruction = tuple[
    tuple[ClipPathSegment, ...],
    tuple[float, float, float, float, float, float],
    bool,
]
ClipPathSnapshot = tuple[ClipPathInstruction, ...]

EMPTY_PASSTHROUGH_SNAPSHOT = PassthroughSnapshot()
EMPTY_CLIP_PATH_SNAPSHOT: ClipPathSnapshot = ()


def append_passthrough_instruction(
    snapshot: PassthroughSnapshot,
    instruction: PassthroughInstruction,
) -> PassthroughSnapshot:
    if _is_snapshot(snapshot):
        tail = _PassthroughNode(
            snapshot.tail,
            instruction,
            None,
            0,
            snapshot.event_count + 1,
        )
        return PassthroughSnapshot(
            tail,
            snapshot.latest_versions,
            snapshot.next_version,
        )
    return (*snapshot, instruction)


def replace_first_passthrough_operator(
    snapshot: PassthroughSnapshot,
    operator: str,
    instruction: PassthroughInstruction,
) -> PassthroughSnapshot:
    if _is_snapshot(snapshot):
        version = snapshot.next_version
        tail = _PassthroughNode(
            snapshot.tail,
            instruction,
            operator,
            version,
            snapshot.event_count + 1,
        )
        latest_versions = (
            *_latest_without_operator(snapshot.latest_versions, operator),
            (operator, version),
        )
        return PassthroughSnapshot(tail, latest_versions, version + 1)
    for index, (op, _arg) in enumerate(snapshot):
        if op == operator:
            return (*snapshot[:index], *snapshot[index + 1 :], instruction)
    return append_passthrough_instruction(snapshot, instruction)


def remove_latest_passthrough_instruction(
    snapshot: PassthroughSnapshot,
) -> PassthroughSnapshot:
    if _is_snapshot(snapshot):
        instructions = snapshot.to_tuple()
        if not instructions:
            return snapshot
        replaceable_operators = {op for op, _version in snapshot.latest_versions}
        rebuilt = EMPTY_PASSTHROUGH_SNAPSHOT
        for op, arg in instructions[:-1]:
            if op in replaceable_operators:
                rebuilt = replace_first_passthrough_operator(rebuilt, op, (op, arg))
            else:
                rebuilt = append_passthrough_instruction(rebuilt, (op, arg))
        return rebuilt
    if not snapshot:
        return snapshot
    return snapshot[:-1]


def freeze_clip_path(clip_path) -> tuple[ClipPathSegment, ...]:
    return tuple(tuple(segment) for segment in clip_path)


def append_clip_path_instruction(
    snapshot: ClipPathSnapshot,
    clip_path,
    ctm: tuple[float, float, float, float, float, float],
    evenodd: bool,
) -> ClipPathSnapshot:
    return (*snapshot, (freeze_clip_path(clip_path), tuple(ctm), evenodd))


def _is_identity_matrix(
    matrix: tuple[float, float, float, float, float, float],
) -> bool:
    return all(
        math.isclose(a, b, rel_tol=0.0, abs_tol=1e-9)
        for a, b in zip(matrix, (1.0, 0.0, 0.0, 1.0, 0.0, 0.0), strict=True)
    )


def _invert_matrix(
    matrix: tuple[float, float, float, float, float, float],
) -> tuple[float, float, float, float, float, float]:
    a, b, c, d, e, f = matrix
    det = a * d - b * c
    if math.isclose(det, 0.0, rel_tol=0.0, abs_tol=1e-12):
        raise ValueError("matrix is not invertible")
    return (
        d / det,
        -b / det,
        -c / det,
        a / det,
        (c * f - d * e) / det,
        (b * e - a * f) / det,
    )


def _multiply_matrices(
    left: tuple[float, float, float, float, float, float],
    right: tuple[float, float, float, float, float, float],
) -> tuple[float, float, float, float, float, float]:
    a1, b1, c1, d1, e1, f1 = left
    a0, b0, c0, d0, e0, f0 = right
    return (
        a1 * a0 + c1 * b0,
        b1 * a0 + d1 * b0,
        a1 * c0 + c1 * d0,
        b1 * c0 + d1 * d0,
        a1 * e0 + c1 * f0 + e1,
        b1 * e0 + d1 * f0 + f1,
    )


def _matrix_to_pdf(matrix: tuple[float, float, float, float, float, float]) -> str:
    return (
        f"{matrix[0]:.6f} {matrix[1]:.6f} {matrix[2]:.6f} "
        f"{matrix[3]:.6f} {matrix[4]:.6f} {matrix[5]:.6f} cm"
    )


def _render_passthrough_instructions(
    instructions: tuple[PassthroughInstruction, ...],
    *,
    include_clipping: bool = False,
    target_ctm_for_extgstate: tuple[float, float, float, float, float, float]
    | None = None,
) -> str:
    def should_keep(op: str) -> bool:
        if include_clipping:
            return True
        return op not in ("W n", "W* n")

    rendered: list[str] = []
    for op, arg in instructions:
        if not should_keep(op):
            continue
        if (
            op == "gs"
            and target_ctm_for_extgstate is not None
            and isinstance(arg, CtmAwarePassthroughArg)
            and arg.ctm != target_ctm_for_extgstate
        ):
            try:
                to_source = _multiply_matrices(
                    arg.ctm,
                    _invert_matrix(target_ctm_for_extgstate),
                )
                if not _is_identity_matrix(to_source):
                    to_target = _invert_matrix(to_source)
                    rendered.append(_matrix_to_pdf(to_source))
                    rendered.append(f"{arg.value} {op}")
                    rendered.append(_matrix_to_pdf(to_target))
                    continue
            except ValueError:
                pass
        rendered.append(f"{arg} {op}")
    return " ".join(rendered)


@functools.lru_cache(maxsize=16384)
def _render_tuple_passthrough_snapshot(
    snapshot: tuple[PassthroughInstruction, ...],
    *,
    include_clipping: bool = False,
) -> str:
    return _render_passthrough_instructions(snapshot, include_clipping=include_clipping)


def render_passthrough_snapshot(
    snapshot: PassthroughSnapshot | tuple[PassthroughInstruction, ...],
    *,
    include_clipping: bool = False,
    target_ctm_for_extgstate: tuple[float, float, float, float, float, float]
    | None = None,
) -> str:
    if target_ctm_for_extgstate is not None:
        instructions = (
            snapshot.to_tuple() if _is_snapshot(snapshot) else tuple(snapshot)
        )
        return _render_passthrough_instructions(
            instructions,
            include_clipping=include_clipping,
            target_ctm_for_extgstate=target_ctm_for_extgstate,
        )
    if _is_snapshot(snapshot):
        return snapshot.render(include_clipping=include_clipping)
    return _render_tuple_passthrough_snapshot(
        tuple(snapshot),
        include_clipping=include_clipping,
    )


def indirect(obj):
    if isinstance(obj, tuple) and obj[0] == "xref":
        return int(obj[1].split(" ")[0])


def get_char_cbox(face, idx):
    g = face.get_char_index(idx)
    return get_glyph_bbox(face, g)


def get_name_cbox(face, name):
    if name:
        if isinstance(name, str):
            name = name.encode("utf-8")
        g = face.get_name_index(name)
        return get_glyph_bbox(face, g)
    return (0, 0, 0, 0)


def font_encoding_lookup(doc, idx, key):
    obj = doc.xref_get_key(idx, key)
    if obj[0] == "name":
        enc_name = obj[1][1:]
        if enc_vector := get_type1_encoding(enc_name):
            return enc_name, enc_vector


def parse_font_encoding(doc, idx):
    if encoding := font_encoding_lookup(doc, idx, "Encoding/BaseEncoding"):
        return encoding
    if encoding := font_encoding_lookup(doc, idx, "Encoding"):
        return encoding
    return ("Custom", get_type1_encoding("StandardEncoding"))


def get_truetype_ansi_bbox_list(face):
    scale = 1000 / face.units_per_EM
    bbox_list = [get_char_cbox(face, code) for code in WinAnsiEncoding]
    bbox_list = [[v * scale for v in bbox] for bbox in bbox_list]
    return bbox_list


def collect_face_cmap(face):
    umap = []
    lmap = []
    for cmap in face.charmaps:
        if cmap.encoding_name == "FT_ENCODING_UNICODE":
            umap.append(cmap)
        else:
            lmap.append(cmap)
    return umap, lmap


def get_truetype_custom_bbox_list(face):
    umap, lmap = collect_face_cmap(face)
    if umap:
        face.set_charmap(umap[0])
    elif lmap:
        face.set_charmap(lmap[0])
    else:
        return []
    scale = 1000 / face.units_per_EM
    bbox_list = [get_char_cbox(face, code) for code in range(256)]
    bbox_list = [[v * scale for v in bbox] for bbox in bbox_list]
    return bbox_list


def parse_font_file(doc, idx, encoding, differences):
    bbox_list = []
    data = doc.xref_stream(idx)
    face = freetype.Face(BytesIO(data))
    if face.get_format() == b"TrueType":
        if encoding[0] == "WinAnsiEncoding":
            return get_truetype_ansi_bbox_list(face)
        if encoding[0] == "Custom":
            return get_truetype_custom_bbox_list(face)
    glyph_name_set = set()
    for x in range(0, face.num_glyphs):
        glyph_name_set.add(face.get_glyph_name(x).decode("U8"))
    scale = 1000 / face.units_per_EM
    enc_name, enc_vector = encoding
    _, lmap = collect_face_cmap(face)
    abbr = enc_name.removesuffix("Encoding")
    if lmap and abbr in ["Custom", "MacRoman", "Standard", "WinAnsi", "MacExpert"]:
        face.set_charmap(lmap[0])
    for i, x in enumerate(enc_vector):
        if x in glyph_name_set:
            v = get_name_cbox(face, x.encode("U8"))
        else:
            v = get_char_cbox(face, i)
        bbox_list.append(v)
    if differences:
        for code, name in differences:
            bbox_list[code] = get_name_cbox(face, name.encode("U8"))
    norm_bbox_list = [[v * scale for v in box] for box in bbox_list]
    return norm_bbox_list


def parse_encoding(obj_str):
    delta = []
    current = 0
    for x in re.finditer(
        r"(?P<p>[\[\]])|(?P<c>\d+)|(?P<n>/[^\s/\[\]()<>]+)|(?P<s>.)", obj_str
    ):
        key = x.lastgroup
        val = x.group()
        if key == "c":
            current = int(val)
        if key == "n":
            delta.append((current, val[1:]))
            current += 1
    return delta


def parse_mapping(text):
    mapping = []
    for x in re.finditer(r"<(?P<num>[a-fA-F0-9]+)>", text):
        mapping.append(x.group("num"))
    return mapping


def update_cmap_pair(cmap, data):
    for start_str, stop_str, value_str in batched(data, 3):
        start = int(start_str, 16)
        stop = int(stop_str, 16)
        try:
            value = base64.b16decode(value_str, True).decode("UTF-16-BE")
            for code in range(start, stop + 1):
                cmap[code] = value
        except Exception:
            pass


def update_cmap_code(cmap, data):
    for code_str, value_str in batched(data, 2):
        code = int(code_str, 16)
        try:
            value = base64.b16decode(value_str, True).decode("UTF-16-BE")
            cmap[code] = value
        except Exception:
            pass


def parse_cmap(cmap_str):
    cmap = {}
    for x in re.finditer(
        r"\s+beginbfrange\s*(?P<r>(<[0-9a-fA-F]+>\s*)+)endbfrange\s+",
        cmap_str,
    ):
        update_cmap_pair(cmap, parse_mapping(x.group("r")))
    for x in re.finditer(
        r"\s+beginbfchar\s*(?P<c>(<[0-9a-fA-F]+>\s*)+)endbfchar",
        cmap_str,
    ):
        update_cmap_code(cmap, parse_mapping(x.group("c")))
    return cmap


unicode_spaces = [
    "\u0020",
    "\u00a0",
    "\u1680",
    "\u2000",
    "\u2001",
    "\u2002",
    "\u2003",
    "\u2004",
    "\u2005",
    "\u2006",
    "\u2007",
    "\u2008",
    "\u2009",
    "\u200a",
    "\u202f",
    "\u205f",
    "\u3000",
    "\u200b",
    "\u2060",
    "\t",
]
pattern = "^[" + "".join(unicode_spaces) + "]+$"
space_regex = re.compile(pattern)


def get_rotation_angle(matrix):
    a, b, c, d, e, f = matrix
    _ = (c, d, e, f)
    angle_rad = math.atan2(b, a)
    angle_deg = math.degrees(angle_rad)
    return angle_deg
