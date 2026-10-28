import numpy as np
import pymupdf

from babeldoc.const import get_process_pool
from babeldoc.format.pdf.document_il.utils.raster_geometry import DEFAULT_MAX_PIXELS
from babeldoc.format.pdf.document_il.utils.raster_geometry import RasterGeometry
from babeldoc.format.pdf.document_il.utils.raster_geometry import with_pixel_budget


def get_no_rotation_img(page: pymupdf.Page, dpi: int = 72) -> pymupdf.Pixmap:
    # return page.get_pixmap(dpi=72)
    original_rotation = page.rotation
    page.set_rotation(0)
    pix = page.get_pixmap(dpi=dpi)
    page.set_rotation(original_rotation)
    return pix


def get_no_rotation_img_multiprocess_internal(
    pdf_bytes: str, pagenum: int, dpi: int = 72
) -> np.ndarray:
    # return page.get_pixmap(dpi=72)
    doc = pymupdf.open(pdf_bytes)
    try:
        page = doc[pagenum]
        original_rotation = page.rotation
        page.set_rotation(0)
        pix = page.get_pixmap(dpi=dpi)
        page.set_rotation(original_rotation)
        return np.frombuffer(pix.samples, np.uint8).reshape(
            pix.height,
            pix.width,
            3,
        )[:, :, ::-1]
    finally:
        doc.close()


def get_no_rotation_img_multiprocess(pdf_bytes: str, pagenum: int, dpi: int = 72):
    pool = get_process_pool()
    if pool is None:
        return get_no_rotation_img_multiprocess_internal(pdf_bytes, pagenum, dpi)
    return pool.apply(
        get_no_rotation_img_multiprocess_internal, (pdf_bytes, pagenum, dpi)
    )


def get_no_rotation_raster_geometry_multiprocess_internal(
    pdf_path: str,
    page_number: int,
    requested_dpi: int = 150,
    max_pixels: int = DEFAULT_MAX_PIXELS,
) -> RasterGeometry:
    """Render one temporary-PDF page with the shared pixel-budget contract."""

    with pymupdf.open(pdf_path) as doc:
        return with_pixel_budget(
            doc[page_number],
            requested_dpi,
            max_pixels,
            normalize_rotation=True,
        )


def get_no_rotation_raster_geometry_multiprocess(
    pdf_path: str,
    page_number: int,
    requested_dpi: int = 150,
    max_pixels: int = DEFAULT_MAX_PIXELS,
) -> RasterGeometry:
    """Return a picklable, budgeted RGB raster geometry for one PDF page."""

    pool = get_process_pool()
    args = (pdf_path, page_number, requested_dpi, max_pixels)
    if pool is None:
        return get_no_rotation_raster_geometry_multiprocess_internal(*args)
    return pool.apply(get_no_rotation_raster_geometry_multiprocess_internal, args)
