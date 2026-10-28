from __future__ import annotations

import math
from typing import Any

from babeldoc.format.pdf.babelpdf.type3 import Type3FontMetrics
from babeldoc.format.pdf.document_il import il_version_1


def build_type3_pdf_font_fields(
    *,
    font_subtype: str | None,
    metrics: Type3FontMetrics | None,
    fallback_ascent: float | None,
    fallback_descent: float | None,
) -> dict[str, Any]:
    if font_subtype != "Type3" or metrics is None:
        return {
            "font_subtype": font_subtype,
            "type3_font_matrix": None,
            "type3_font_bbox": None,
            "type3_em_height": None,
            "ascent": fallback_ascent,
            "descent": fallback_descent,
        }
    return {
        "font_subtype": font_subtype,
        "type3_font_matrix": metrics.font_matrix_text,
        "type3_font_bbox": metrics.font_bbox_text,
        "type3_em_height": metrics.em_height,
        "ascent": metrics.ascent,
        "descent": metrics.descent,
    }


def effective_type3_font_size(
    font: il_version_1.PdfFont | None,
    raw_font_size: float,
) -> float:
    em_height = getattr(font, "type3_em_height", None)
    if not em_height or not math.isfinite(float(em_height)) or float(em_height) <= 0:
        return raw_font_size
    return raw_font_size * float(em_height)


def inverse_type3_font_size_for_tf(
    font: il_version_1.PdfFont | None,
    effective_font_size: float,
) -> float:
    if getattr(font, "font_subtype", None) != "Type3":
        return effective_font_size
    em_height = getattr(font, "type3_em_height", None)
    if not em_height or not math.isfinite(float(em_height)) or float(em_height) <= 0:
        return effective_font_size
    return effective_font_size / float(em_height)
