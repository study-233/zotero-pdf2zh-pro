from __future__ import annotations

import base64
import os
import unicodedata
from concurrent.futures import FIRST_COMPLETED
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import wait
from dataclasses import dataclass
from typing import Any

import cv2
import httpx
import numpy as np
import pymupdf

from babeldoc.docvision.base_doclayout import DocLayoutModel
from babeldoc.docvision.base_doclayout import YoloBox
from babeldoc.docvision.base_doclayout import YoloResult
from babeldoc.format.pdf.document_il import il_version_1
from babeldoc.format.pdf.document_il.utils.extract_char import (
    convert_page_to_char_boxes,
)
from babeldoc.format.pdf.document_il.utils.extract_char import (
    process_page_chars_to_lines,
)
from babeldoc.format.pdf.document_il.utils.fontmap import FontMapper
from babeldoc.format.pdf.document_il.utils.layout_helper import SPACE_REGEX
from babeldoc.format.pdf.document_il.utils.raster_geometry import RasterGeometry
from babeldoc.format.pdf.document_il.utils.raster_geometry import with_pixel_budget

DPI = 150
LAYOUT_ERROR = "layout 解析失败"


def _positive_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _positive_float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be positive") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _encode_image(image: np.ndarray) -> bytes:
    image_bgr = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
    ok, encoded = cv2.imencode(".jpg", image_bgr)
    if not ok:
        raise RuntimeError(LAYOUT_ERROR)
    return encoded.tobytes()


def _clip_num(num: float, min_value: float, max_value: float) -> float:
    if num < min_value:
        return min_value
    if num > max_value:
        return max_value
    return num


def _filter_text(text: str, font_mapper: FontMapper) -> str:
    normalize = unicodedata.normalize("NFKC", text)
    unicodes = [char for char in normalize if font_mapper.has_char(char)]
    normalize = "".join(unicodes)
    return SPACE_REGEX.sub(" ", normalize).strip()


@dataclass(frozen=True)
class _PageInput:
    index: int
    page: il_version_1.Page
    image: np.ndarray
    image_data: bytes
    geometry: RasterGeometry
    line_results: list[dict[str, Any]] | None


class RpcDocLayoutModel(DocLayoutModel):
    def __init__(self, host: str, requires_line_extraction: bool = False):
        if not isinstance(host, str) or not host:
            raise ValueError("rpc_doclayout8 host is required")
        if ";" in host:
            raise ValueError("rpc_doclayout8 host must be a single task-local URL")
        self.host = host.rstrip("/")
        self.requires_line_extraction = bool(requires_line_extraction)
        self.layout_page_input_buffer_limit = _positive_int_env(
            "BABELDOC_EXECUTOR_LAYOUT_PAGE_INPUT_BUFFER_LIMIT",
            2,
        )
        self.line_extraction_max_workers = _positive_int_env(
            "BABELDOC_EXECUTOR_LAYOUT_LINE_EXTRACTION_MAX_WORKERS",
            1,
        )
        self.layout_request_max_workers = _positive_int_env(
            "BABELDOC_EXECUTOR_LAYOUT_REQUEST_MAX_WORKERS",
            8,
        )
        self.layout_request_timeout_seconds = _positive_float_env(
            "BABELDOC_EXECUTOR_LAYOUT_REQUEST_TIMEOUT_SECONDS",
            600.0,
        )
        self.font_mapper: FontMapper | None = None

    @property
    def stride(self) -> int:
        return 32

    def init_font_mapper(self, translation_config) -> None:
        if self.requires_line_extraction:
            self.font_mapper = FontMapper(translation_config)

    def handle_document(
        self,
        pages: list[il_version_1.Page],
        mupdf_doc: pymupdf.Document,
        translate_config,
        save_debug_image,
    ):
        with (
            ThreadPoolExecutor(
                max_workers=self.layout_request_max_workers,
            ) as layout_executor,
            ThreadPoolExecutor(
                max_workers=self.line_extraction_max_workers,
            ) as line_executor,
        ):
            futures = {}
            completed = {}
            next_submit = 0
            next_yield = 0
            max_in_flight = (
                self.layout_request_max_workers + self.layout_page_input_buffer_limit
            )

            def fill_window() -> None:
                nonlocal next_submit
                while (
                    next_submit < len(pages)
                    and (len(futures) + len(completed)) < max_in_flight
                ):
                    page_input = self._prepare_page_input(
                        next_submit,
                        pages[next_submit],
                        mupdf_doc,
                        translate_config,
                        line_executor,
                    )
                    future = layout_executor.submit(
                        self._request_layout,
                        page_input,
                        translate_config,
                    )
                    futures[future] = next_submit
                    next_submit += 1

            fill_window()
            while next_yield < len(pages):
                if next_yield in completed:
                    yield completed.pop(next_yield)
                    next_yield += 1
                    fill_window()
                    continue

                done, _pending = wait(futures.keys(), return_when=FIRST_COMPLETED)
                for future in done:
                    index = futures.pop(future)
                    try:
                        completed[index] = future.result()
                    except Exception as exc:
                        raise RuntimeError(LAYOUT_ERROR) from exc
                fill_window()

    def _prepare_page_input(
        self,
        index: int,
        page: il_version_1.Page,
        mupdf_doc: pymupdf.Document,
        translate_config,
        line_executor: ThreadPoolExecutor,
    ) -> _PageInput:
        translate_config.raise_if_cancelled()
        line_future = (
            line_executor.submit(self._extract_raw_lines, page)
            if self.requires_line_extraction
            else None
        )
        geometry = with_pixel_budget(
            mupdf_doc[page.page_number],
            DPI,
            normalize_rotation=True,
        )
        image = geometry.image
        image_data = _encode_image(image)
        line_results = (
            self._convert_line_results(
                line_future.result(),
                geometry,
            )
            if line_future is not None
            else None
        )
        return _PageInput(index, page, image, image_data, geometry, line_results)

    def _extract_raw_lines(self, page: il_version_1.Page):
        if self.font_mapper is None:
            raise RuntimeError(LAYOUT_ERROR)

        char_boxes = convert_page_to_char_boxes(page)
        return process_page_chars_to_lines(char_boxes)

    def _convert_line_results(
        self,
        lines,
        geometry: RasterGeometry,
    ) -> list[dict[str, Any]]:
        results = []
        for line in lines:
            converted = self._convert_line(line, geometry)
            if converted is not None:
                results.append(converted)
        return results

    def _convert_line(
        self,
        line,
        geometry: RasterGeometry,
    ) -> dict[str, Any] | None:
        if not line.text or self.font_mapper is None:
            return None
        boxes = [char[0] for char in line.chars]
        min_x = min(box.x for box in boxes)
        max_x = max(box.x2 for box in boxes)
        min_y = min(box.y for box in boxes)
        max_y = max(box.y2 for box in boxes)

        min_x = geometry.pt_len_to_px(min_x, "x")
        max_x = geometry.pt_len_to_px(max_x, "x")
        min_y = geometry.pt_len_to_px(min_y, "y")
        max_y = geometry.pt_len_to_px(max_y, "y")
        image_width = geometry.pixel_width
        image_height = geometry.pixel_height
        min_y, max_y = image_height - max_y, image_height - min_y

        box_volume = (max_x - min_x) * (max_y - min_y)
        if box_volume < 1:
            return None

        min_x = _clip_num(min_x, 0, image_width - 1)
        max_x = _clip_num(max_x, 0, image_width - 1)
        min_y = _clip_num(min_y, 0, image_height - 1)
        max_y = _clip_num(max_y, 0, image_height - 1)

        filtered_text = _filter_text(line.text, self.font_mapper)
        if not filtered_text:
            return None

        return {
            "box": [min_x, min_y, max_x, max_y],
            "text": filtered_text,
        }

    def _request_layout(
        self,
        page_input: _PageInput,
        translate_config,
    ):
        translate_config.raise_if_cancelled()
        request_body: dict[str, Any] = {
            "schema_version": 1,
            "page_number": page_input.page.page_number,
            "dpi": page_input.geometry.render_dpi,
            "image": base64.b64encode(page_input.image_data).decode("utf-8"),
            "image_size": [page_input.image.shape[1], page_input.image.shape[0]],
        }
        if self.requires_line_extraction:
            request_body["line_results"] = page_input.line_results or []

        try:
            response = httpx.post(
                f"{self.host}/inference",
                json=request_body,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                timeout=self.layout_request_timeout_seconds,
                follow_redirects=True,
            )
            if response.status_code < 200 or response.status_code >= 300:
                raise RuntimeError(LAYOUT_ERROR)
            payload = response.json()
            yolo_result = self._parse_response(payload, page_input.geometry)
            return page_input.page, yolo_result
        except Exception as exc:
            raise RuntimeError(LAYOUT_ERROR) from exc

    def _parse_response(
        self,
        payload: Any,
        geometry: RasterGeometry | int,
        _image_height: int | None = None,
    ) -> YoloResult:
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise RuntimeError(LAYOUT_ERROR)
        boxes_payload = payload.get("boxes")
        if not isinstance(boxes_payload, list):
            raise RuntimeError(LAYOUT_ERROR)

        names: dict[int, str] = {}
        boxes: list[YoloBox] = []
        for item in boxes_payload:
            if not isinstance(item, dict):
                raise RuntimeError(LAYOUT_ERROR)
            class_id = item.get("class_id")
            label = item.get("label")
            score = item.get("score")
            box = item.get("box")
            if (
                not isinstance(class_id, int)
                or not isinstance(label, str)
                or not isinstance(score, int | float)
                or not isinstance(box, list)
                or len(box) != 4
            ):
                raise RuntimeError(LAYOUT_ERROR)
            if isinstance(geometry, RasterGeometry):
                coords = [
                    geometry.px_len_to_pt(float(value), "x" if index % 2 == 0 else "y")
                    for index, value in enumerate(box)
                ]
            else:
                # Keep the narrow legacy direct-call compatibility used by
                # old callers/tests; all request-path calls pass RasterGeometry.
                coords = [float(value) * 72 / DPI for value in box]
            names[class_id] = label
            boxes.append(
                YoloBox(
                    None,
                    np.array(coords),
                    np.array(float(score)),
                    class_id,
                )
            )
        return YoloResult(boxes=boxes, names=names)

    @staticmethod
    def from_host(host: str) -> RpcDocLayoutModel:
        return RpcDocLayoutModel(host=host, requires_line_extraction=False)
