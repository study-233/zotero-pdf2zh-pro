from __future__ import annotations

import math
from dataclasses import dataclass

from babeldoc.format.pdf.new_parser.pdf_token_serializer import serialize_pdf_token
from babeldoc.format.pdf.new_parser.tokenizer import PdfOperation

GRAPHIC_OPERATORS = {
    "m",
    "l",
    "c",
    "v",
    "y",
    "re",
    "h",
    "S",
    "s",
    "f",
    "f*",
    "F",
    "B",
    "B*",
    "b",
    "b*",
    "n",
    "Do",
}
FILTERED_ARGFUL_OPERATORS = {'"', "'", "EI", "MP", "DP", "BMC", "BDC"}
FILTERED_ARGLESS_OPERATORS = {"BI", "ID", "EMC", "INLINE_IMAGE"}


@dataclass(frozen=True)
class BaseOperationSidecar:
    page_inner_operation: str
    xobject_end_operations: dict[tuple[str, ...], str]


def collect_page_base_inner_operation(operations: list[PdfOperation]) -> str:
    parts: list[str] = []
    for operation in operations:
        operator = operation.operator
        if operator in GRAPHIC_OPERATORS:
            continue
        if operator == "d":
            if len(operation.operands) != 2:
                continue
            arg0 = _serialize_dash_pattern(operation.operands[0])
            arg1 = _serialize_operand(operation.operands[1])
            parts.append(f"{arg0} {arg1} {operator}")
            continue
        if operator.startswith("T") or operator in FILTERED_ARGFUL_OPERATORS:
            continue
        if not operation.operands and operator in FILTERED_ARGLESS_OPERATORS:
            continue
        if not operation.operands:
            parts.append(operator)
            continue
        parts.append(
            f"{' '.join(_serialize_operand(operand) for operand in operation.operands)} {operator}"
        )
    return " ".join(part for part in parts if part)


def wrap_page_base_operation(
    page_inner_operation: str,
    cropbox: tuple[float, float, float, float],
) -> str:
    x0, y0, _x1, _y1 = cropbox
    ctm_for_ops = (1.0, 0.0, 0.0, 1.0, -x0, -y0)
    return f"q {page_inner_operation} Q {' '.join(f'{x:f}' for x in ctm_for_ops)} cm"


def compute_xobject_end_operation(
    matrix: tuple[float, float, float, float, float, float],
    parent_ctm: tuple[float, float, float, float, float, float],
) -> str:
    ma, mb, mc, md, me, mf = matrix
    pa, pb, pc, pd, pe, pf = parent_ctm
    a = ma * pa + mb * pc
    b = ma * pb + mb * pd
    c = mc * pa + md * pc
    d = mc * pb + md * pd
    e = me * pa + mf * pc + pe
    f = me * pb + mf * pd + pf
    det = a * d - b * c
    if math.isclose(det, 0.0):
        return " "
    inv_a = d / det
    inv_b = -b / det
    inv_c = -c / det
    inv_d = a / det
    inv_e = -(e * inv_a + f * inv_c)
    inv_f = -(e * inv_b + f * inv_d)
    return (
        f"{inv_a:.6f} {inv_b:.6f} {inv_c:.6f} {inv_d:.6f} {inv_e:.6f} {inv_f:.6f} cm "
    )


def _serialize_dash_pattern(operand: object) -> str:
    if isinstance(operand, list | tuple):
        return f"[{' '.join(_serialize_operand(item) for item in operand)}]"
    return _serialize_operand(operand)


def _serialize_operand(operand: object) -> str:
    return serialize_pdf_token(operand).replace("'", "")
