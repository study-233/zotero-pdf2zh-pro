import logging
import math
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np
from pymupdf import Document

import babeldoc.format.pdf.document_il.utils.extract_char
from babeldoc.format.pdf.document_il import il_version_1
from babeldoc.format.pdf.document_il.utils.style_helper import GREEN
from babeldoc.format.pdf.translation_config import TranslationConfig

logger = logging.getLogger(__name__)


class LayoutParser:
    stage_name = "Parse Page Layout"

    def __init__(self, translation_config: TranslationConfig):
        self.translation_config = translation_config
        self.model = translation_config.doc_layout_model

    def _save_debug_image(self, image: np.ndarray, layout, page_number: int):
        """Save debug image with drawn boxes if debug mode is enabled."""
        if not self.translation_config.debug:
            return

        debug_dir = Path(self.translation_config.get_working_file_path("ocr-box-image"))
        debug_dir.mkdir(parents=True, exist_ok=True)

        # Draw boxes on the image
        debug_image = image.copy()
        for box in layout.boxes:
            x0, y0, x1, y1 = box.xyxy
            cv2.rectangle(
                debug_image,
                (int(x0), int(y0)),
                (int(x1), int(y1)),
                (0, 255, 0),
                2,
            )
            # Add text label
            cv2.putText(
                debug_image,
                layout.names[box.cls],
                (int(x0), int(y0) - 5),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 255, 0),
                1,
            )
        img_bgr = cv2.cvtColor(debug_image, cv2.COLOR_RGB2BGR)

        # Save the image
        output_path = debug_dir / f"{page_number}.jpg"
        cv2.imwrite(str(output_path), img_bgr)

    def _save_debug_box_to_page(self, page: il_version_1.Page):
        """Save debug boxes and text labels to the PDF page."""
        if not self.translation_config.debug:
            return

        color = GREEN

        for layout in page.page_layout:
            # Create a rectangle box
            scale_factor = 1
            if layout.class_name == "fallback_line":
                scale_factor = 0.1
            rect = il_version_1.PdfRectangle(
                box=il_version_1.Box(
                    x=layout.box.x,
                    y=layout.box.y,
                    x2=layout.box.x2,
                    y2=layout.box.y2,
                ),
                graphic_state=color,
                debug_info=True,
                line_width=0.4 * scale_factor,
            )
            page.pdf_rectangle.append(rect)

            # Create text label at top-left corner
            # Note: PDF coordinates are from bottom-left,
            # so we use y2 for top position
            style = il_version_1.PdfStyle(
                font_id="base",
                font_size=4 * scale_factor,
                graphic_state=color,
            )
            page.pdf_paragraph.append(
                il_version_1.PdfParagraph(
                    first_line_indent=False,
                    box=il_version_1.Box(
                        x=layout.box.x,
                        y=layout.box.y2,
                        x2=layout.box.x2,
                        y2=layout.box.y2 + 5,
                    ),
                    vertical=False,
                    pdf_style=style,
                    unicode=layout.class_name,
                    pdf_paragraph_composition=[
                        il_version_1.PdfParagraphComposition(
                            pdf_same_style_unicode_characters=il_version_1.PdfSameStyleUnicodeCharacters(
                                unicode=layout.class_name,
                                pdf_style=style,
                                debug_info=True,
                            ),
                        ),
                    ],
                    xobj_id=-1,
                ),
            )

    def process(self, docs: il_version_1.Document, mupdf_doc: Document):
        """Generate layouts for all pages that need to be translated."""
        # Get pages that need to be translated
        total = len(docs.page)
        with self.translation_config.progress_monitor.stage_start(
            self.stage_name,
            total * 2,
        ) as progress:
            # Process predictions for each page
            for page, layouts in self.model.handle_document(
                docs.page,
                mupdf_doc,
                self.translation_config,
                self._save_debug_image,
            ):
                page_layouts = []
                for layout in layouts.boxes:
                    # Convert coordinate system from picture to il
                    # system to the il coordinate system
                    x0, y0, x1, y1 = layout.xyxy
                    # pix = get_no_rotation_img(mupdf_doc[page.page_number])
                    # pix = mupdf_doc[page.page_number].get_pixmap()
                    # h, w = pix.height, pix.width
                    box = mupdf_doc[page.page_number].mediabox_size
                    b_h = math.ceil(box.y)
                    b_w = math.ceil(box.x)
                    # if b_h != h or b_w != w:
                    #     logger.warning(f"page {page.page_number} mediabox is not correct, b_h: {b_h}, h: {h}, b_w: {b_w}, w: {w}")
                    h, w = b_h, b_w
                    x0, y0, x1, y1 = (
                        np.clip(int(x0 - 1), 0, w - 1),
                        np.clip(int(h - y1 - 1), 0, h - 1),
                        np.clip(int(x1 + 1), 0, w - 1),
                        np.clip(int(h - y0 + 1), 0, h - 1),
                    )
                    page_layout = il_version_1.PageLayout(
                        id=len(page_layouts) + 1,
                        box=il_version_1.Box(
                            x0.item(),
                            y0.item(),
                            x1.item(),
                            y1.item(),
                        ),
                        conf=layout.conf.item(),
                        class_name=layouts.names[layout.cls],
                    )
                    page_layouts.append(page_layout)

                page.page_layout = page_layouts
                self.add_native_table_cells(page, mupdf_doc[page.page_number])
                # self.generate_fallback_line_layout_for_page(page)
                # self._save_debug_box_to_page(page)
                progress.advance(1)
            with ThreadPoolExecutor(max_workers=os.cpu_count()) as executor:
                for page in docs.page:
                    executor.submit(
                        self.generate_fallback_line_layout_for_page, page, progress
                    )
        return docs

    def add_native_table_cells(self, page, source_page):
        """Keep text from one ruled cell together instead of translating fallback lines."""
        if not getattr(self.translation_config, "translate_table_text", False):
            return
        regions = [layout.box for layout in page.page_layout if layout.class_name == "table"]
        # The IL layout coordinates use an unrotated, uncropped MediaBox.
        if (not regions or source_page.rotation or source_page.cropbox != source_page.mediabox
                or source_page.mediabox.x0 or source_page.mediabox.y0):
            return
        try:
            tables = source_page.find_tables(strategy="lines_strict").tables
        except Exception as error:
            logger.warning("native table detection failed: error_type=%s", type(error).__name__)
            return
        height = source_page.mediabox.height
        next_id = max((layout.id for layout in page.page_layout), default=0) + 1
        for table in tables:
            if table.row_count < 2 or table.col_count < 2:
                continue
            x0, y0, x1, y1 = table.bbox
            box = il_version_1.Box(x0, height - y1, x1, height - y0)
            area = (x1 - x0) * (y1 - y0)
            if area <= 0 or not any(
                max(0, min(box.x2, r.x2) - max(box.x, r.x))
                * max(0, min(box.y2, r.y2) - max(box.y, r.y)) >= area * .9
                for r in regions
            ):
                continue
            for cell in table.cells:
                if cell is None:
                    continue
                x0, y0, x1, y1 = cell
                page.page_layout.append(il_version_1.PageLayout(
                    id=next_id, box=il_version_1.Box(x0, height - y1, x1, height - y0),
                    conf=1.0, class_name="wired_table_cell",
                ))
                next_id += 1

    def generate_fallback_line_layout_for_page(self, page: il_version_1.Page, progress):
        try:
            exists_page_layouts = page.page_layout
            char_boxes = babeldoc.format.pdf.document_il.utils.extract_char.convert_page_to_char_boxes(
                page
            )
            if not char_boxes:
                return

            clusters = babeldoc.format.pdf.document_il.utils.extract_char.process_page_chars_to_lines(
                char_boxes
            )
            for cluster in clusters:
                boxes = [c[0] for c in cluster.chars]
                min_x = min(b.x for b in boxes)
                max_x = max(b.x2 for b in boxes)
                min_y = min(b.y for b in boxes)
                max_y = max(b.y2 for b in boxes)
                cluster.chars = il_version_1.Box(min_x, min_y, max_x, max_y)
                page_layout = il_version_1.PageLayout(
                    id=len(exists_page_layouts) + 1,
                    box=il_version_1.Box(
                        min_x,
                        min_y,
                        max_x,
                        max_y,
                    ),
                    conf=1,
                    class_name="fallback_line",
                )
                exists_page_layouts.append(page_layout)
            self._save_debug_box_to_page(page)
        finally:
            progress.advance(1)
