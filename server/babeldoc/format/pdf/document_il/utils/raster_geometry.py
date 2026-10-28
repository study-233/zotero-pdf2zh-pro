"""Page rasterization with explicit pixel geometry.

This module owns page-size-to-raster-size calculations and the RGB raster
metadata shared by the supported layout consumers.  It intentionally does not
perform coordinate-origin changes, y-axis flips, clipping, or padding.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Literal

import numpy as np
import pymupdf

logger = logging.getLogger(__name__)

DEFAULT_MAX_PIXELS = 12_000_000
Axis = Literal["x", "y"]


@dataclass(frozen=True)
class RasterGeometry:
    """RGB raster and its scale relative to the rendered PDF page view.

    The page origin is deliberately not represented here.  Callers keep
    their existing origin, y-axis, clipping, and padding behavior.  The
    normal page path is expected to have a zero-origin rect after
    ``fix_media_box``; a non-zero origin is logged but does not stop the
    render.
    """

    image: np.ndarray
    requested_dpi: int
    render_dpi: int
    pixel_width: int
    pixel_height: int
    page_width_pt: float
    page_height_pt: float

    @property
    def x_scale(self) -> float:
        """Return rendered pixels per PDF point on the x axis."""

        return self.pixel_width / self.page_width_pt

    @property
    def y_scale(self) -> float:
        """Return rendered pixels per PDF point on the y axis."""

        return self.pixel_height / self.page_height_pt

    def pt_len_to_px(self, length: float, axis: Axis = "x") -> float:
        """Convert a point-space length to raster pixels by axis."""

        return length * self._scale(axis)

    def px_len_to_pt(self, length: float, axis: Axis = "x") -> float:
        """Convert a raster-pixel length to PDF points by axis."""

        return length / self._scale(axis)

    def _scale(self, axis: Axis) -> float:
        if axis == "x":
            return self.x_scale
        if axis == "y":
            return self.y_scale
        raise ValueError(f"unsupported raster axis: {axis!r}")

    def render_at_dpi(
        self,
        page: pymupdf.Page,
        *,
        normalize_rotation: bool,
    ) -> np.ndarray:
        """Render the page again at this geometry's DPI.

        This is used by DetectScannedFile so its before/after renders share
        the exact same selected DPI while retaining the current rotation
        semantics.
        """

        image, _rect = _render_rgb(page, self.render_dpi, normalize_rotation)
        return image


def _positive_int(value: int, name: str) -> int:
    value = int(value)
    if value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _pixel_dimensions(
    page_width_pt: float, page_height_pt: float, dpi: int
) -> tuple[int, int]:
    """Return the contract pixel dimensions using independent edge ceils."""

    return (
        max(1, math.ceil(page_width_pt * dpi / 72)),
        max(1, math.ceil(page_height_pt * dpi / 72)),
    )


def _render_rgb(
    page: pymupdf.Page,
    dpi: int,
    normalize_rotation: bool,
) -> tuple[np.ndarray, pymupdf.Rect]:
    """Render one page as RGB and restore a temporarily normalized rotation."""

    original_rotation = page.rotation
    try:
        if normalize_rotation:
            page.set_rotation(0)
        # Keep this read after set_rotation(0): rect is the render-view size.
        rect = page.rect
        if rect.x0 != 0 or rect.y0 != 0:
            logger.warning(
                "Raster page rect has non-zero origin: (%.3f, %.3f)",
                rect.x0,
                rect.y0,
            )
        pix = page.get_pixmap(dpi=dpi)
        channels = getattr(pix, "n", 3)
        if channels != 3:
            raise ValueError(f"expected an RGB pixmap, got {channels} channels")
        samples = np.frombuffer(pix.samples, dtype=np.uint8)
        expected_size = pix.width * pix.height * 3
        if samples.size != expected_size:
            raise ValueError(
                "RGB pixmap sample size does not match its dimensions: "
                f"{samples.size} != {expected_size}"
            )
        image = samples.reshape(pix.height, pix.width, 3)
        return image, rect
    finally:
        if normalize_rotation:
            page.set_rotation(original_rotation)


def _make_geometry(
    page: pymupdf.Page,
    requested_dpi: int,
    render_dpi: int,
    *,
    normalize_rotation: bool,
) -> RasterGeometry:
    image, rect = _render_rgb(page, render_dpi, normalize_rotation)
    return RasterGeometry(
        image=image,
        requested_dpi=requested_dpi,
        render_dpi=render_dpi,
        pixel_width=image.shape[1],
        pixel_height=image.shape[0],
        page_width_pt=float(rect.width),
        page_height_pt=float(rect.height),
    )


def with_pixel_budget(
    page: pymupdf.Page,
    requested_dpi: int,
    max_pixels: int = DEFAULT_MAX_PIXELS,
    *,
    normalize_rotation: bool,
) -> RasterGeometry:
    """Render a page at the highest DPI that reaches the pixel budget.

    The initial DPI uses the independent-edge-ceil budget estimate.  The
    actual pixmap dimensions are then checked after every render; DPI is
    reduced one integer at a time until the budget is met or DPI reaches 1.
    A DPI of 1 is intentionally emitted even if the page still exceeds the
    budget.
    """

    requested_dpi = _positive_int(requested_dpi, "requested_dpi")
    max_pixels = _positive_int(max_pixels, "max_pixels")

    original_rotation = page.rotation
    try:
        if normalize_rotation:
            page.set_rotation(0)
        # This read must happen after rotation normalization.
        rect = page.rect
        if rect.x0 != 0 or rect.y0 != 0:
            logger.warning(
                "Raster page rect has non-zero origin: (%.3f, %.3f)",
                rect.x0,
                rect.y0,
            )
        width_pt = float(rect.width)
        height_pt = float(rect.height)
    finally:
        if normalize_rotation:
            page.set_rotation(original_rotation)

    initial_dpi = math.floor(math.sqrt(max_pixels * 72**2 / (width_pt * height_pt)))
    render_dpi = max(1, min(requested_dpi, initial_dpi))

    while True:
        geometry = _make_geometry(
            page,
            requested_dpi,
            render_dpi,
            normalize_rotation=normalize_rotation,
        )
        if geometry.pixel_width * geometry.pixel_height <= max_pixels:
            return geometry
        if render_dpi == 1:
            return geometry
        render_dpi -= 1


def with_target_long_edge(
    page: pymupdf.Page,
    default_dpi: int,
    target_px: int,
    *,
    normalize_rotation: bool,
) -> RasterGeometry:
    """Render normally unless the default long edge exceeds 2× the target."""

    default_dpi = _positive_int(default_dpi, "default_dpi")
    target_px = _positive_int(target_px, "target_px")

    original_rotation = page.rotation
    try:
        if normalize_rotation:
            page.set_rotation(0)
        # This read must happen after rotation normalization.
        rect = page.rect
        if rect.x0 != 0 or rect.y0 != 0:
            logger.warning(
                "Raster page rect has non-zero origin: (%.3f, %.3f)",
                rect.x0,
                rect.y0,
            )
        long_edge_pt = max(float(rect.width), float(rect.height))
    finally:
        if normalize_rotation:
            page.set_rotation(original_rotation)

    default_long_edge_px = long_edge_pt * default_dpi / 72
    if default_long_edge_px > 2 * target_px:
        render_dpi = max(1, math.floor(target_px * 72 / long_edge_pt))
    else:
        render_dpi = default_dpi
    return _make_geometry(
        page,
        default_dpi,
        render_dpi,
        normalize_rotation=normalize_rotation,
    )
