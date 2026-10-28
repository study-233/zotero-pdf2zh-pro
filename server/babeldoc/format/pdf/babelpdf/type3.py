import io
import math
import re
from dataclasses import dataclass

import pymupdf

_PDF_NUMBER_RE = re.compile(r"[+-]?(?:(?:\d+\.\d*)|(?:\.\d+)|(?:\d+))(?:[eE][+-]?\d+)?")


@dataclass(frozen=True, slots=True)
class Type3FontMetrics:
    font_matrix: tuple[float, float, float, float, float, float]
    font_bbox: tuple[float, float, float, float]
    font_matrix_text: str
    font_bbox_text: str
    em_height: float
    ascent: float
    descent: float


def _parse_number_array(text: str, *, expected_len: int) -> tuple[float, ...] | None:
    values = [float(match.group(0)) for match in _PDF_NUMBER_RE.finditer(text)]
    if len(values) < expected_len:
        return None
    values = values[:expected_len]
    if not all(math.isfinite(value) for value in values):
        return None
    return tuple(values)


def _format_number_array(values: tuple[float, ...]) -> str:
    return " ".join(f"{value:.12g}" for value in values)


def _transform_point(
    matrix: tuple[float, float, float, float, float, float],
    point: tuple[float, float],
) -> tuple[float, float]:
    a, b, c, d, e, f = matrix
    x, y = point
    return a * x + c * y + e, b * x + d * y + f


def _transform_bbox(
    matrix: tuple[float, float, float, float, float, float],
    bbox: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = bbox
    points = [
        _transform_point(matrix, (x0, y0)),
        _transform_point(matrix, (x0, y1)),
        _transform_point(matrix, (x1, y0)),
        _transform_point(matrix, (x1, y1)),
    ]
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def build_type3_font_metrics(
    font_matrix: tuple[float, ...],
    font_bbox: tuple[float, ...],
) -> Type3FontMetrics | None:
    if len(font_matrix) < 6 or len(font_bbox) < 4:
        return None
    matrix = tuple(font_matrix[:6])
    bbox = tuple(font_bbox[:4])
    if not all(math.isfinite(value) for value in (*matrix, *bbox)):
        return None
    _, y0, _, y1 = _transform_bbox(matrix, bbox)
    em_height = y1 - y0
    if not math.isfinite(em_height) or em_height <= 0:
        return None
    scale = 1000.0 / em_height
    return Type3FontMetrics(
        font_matrix=matrix,
        font_bbox=bbox,
        font_matrix_text=_format_number_array(matrix),
        font_bbox_text=_format_number_array(bbox),
        em_height=em_height,
        ascent=y1 * scale,
        descent=y0 * scale,
    )


def get_type3_font_metrics(doc, obj: int) -> Type3FontMetrics | None:
    try:
        font_matrix_text = doc.xref_get_key(obj, "FontMatrix")[1]
        font_bbox_text = doc.xref_get_key(obj, "FontBBox")[1]
    except Exception:
        return None
    font_matrix = _parse_number_array(font_matrix_text, expected_len=6)
    font_bbox = _parse_number_array(font_bbox_text, expected_len=4)
    if font_matrix is None or font_bbox is None:
        return None
    return build_type3_font_metrics(font_matrix, font_bbox)


def normalize_type3_bbox_to_1000_em(
    bbox: tuple[float, float, float, float],
    metrics: Type3FontMetrics,
) -> tuple[float, float, float, float]:
    scale = 1000.0 / metrics.em_height
    return tuple(value * scale for value in bbox)


def merge_bbox(bbox_list, factor=1):
    if bbox_list:
        base = bbox_list[0]
        for bbox in bbox_list[1:]:
            base.include_rect(bbox)
        x0, y0, x1, y1 = [v / factor for v in tuple(base)]
        return x0, -y1, x1, -y0


def get_type3_bbox(
    doc,
    obj,
    check_cancelled=lambda: None,
    *,
    normalize_to_1000_em: bool = False,
    metrics: Type3FontMetrics | None = None,
):
    bbox_list = [(0, 0, 0, 0)] * 256
    first = int(doc.xref_get_key(obj, "FirstChar")[1])
    last = int(doc.xref_get_key(obj, "LastChar")[1])
    normalize_metrics = None
    if normalize_to_1000_em:
        metrics = metrics or get_type3_font_metrics(doc, obj)
        normalize_metrics = metrics
        factor = 1
    else:
        factor_text = doc.xref_get_key(obj, "FontMatrix")[1]
        factor = 1
        if factor_m := re.search(_PDF_NUMBER_RE, factor_text):
            factor = float(factor_m.group(0))
    page = doc.new_page(width=10, height=10)
    doc.xref_set_key(page.xref, "Resources", "<<>>")
    doc.xref_set_key(page.xref, "Resources/Font", f"<</T0 {obj} 0 R>>")
    text = doc.get_new_xref()
    doc.update_object(text, "<<>>")
    try:
        for x in range(first, last + 1):
            check_cancelled()
            doc.update_stream(text, b"1 0 0 1 0 10 cm BT /T0 1 Tf <%02X> Tj ET" % x)
            doc.xref_set_key(page.xref, "Contents", f"{text} 0 R")
            char_data = page.get_svg_image(text_as_path=True)
            char_bbox = []
            with pymupdf.Document(stream=io.BytesIO(char_data.encode("U8"))) as char_doc:
                for element in char_doc:
                    for item in element.get_drawings():
                        char_bbox.append(item["rect"])
            if char_bbox_merged := merge_bbox(char_bbox, factor):
                if normalize_metrics is not None:
                    char_bbox_merged = normalize_type3_bbox_to_1000_em(char_bbox_merged, normalize_metrics)
                bbox_list[x] = char_bbox_merged
    finally:
        doc.delete_page(-1)
    return bbox_list
