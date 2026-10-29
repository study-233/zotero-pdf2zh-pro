"""Task-local checkpoint of the immutable IR before term extraction/translation."""
import hashlib
import logging
from dataclasses import fields, is_dataclass
from functools import lru_cache
from pathlib import Path
from typing import get_args, get_origin
from zipfile import ZIP_DEFLATED, ZipFile

import orjson
import pymupdf

from babeldoc.format.pdf.document_il import il_version_1
from babeldoc.format.pdf.document_il.xml_converter import _orjson_default

logger = logging.getLogger(__name__)

# Bump when preprocessing semantics or the IR schema change. The recovery
# fingerprint additionally binds the checkpoint to the document and settings.
VERSION = 1
_SETTINGS = (
    "pages", "page_ranges", "lang_in", "lang_out", "primary_font_family", "formular_font_pattern",
    "formular_char_pattern", "split_short_lines", "short_line_split_factor",
    "skip_scanned_detection", "ocr_workaround", "auto_enable_ocr_workaround",
    "only_include_translated_page", "enable_graphic_element_process",
    "merge_alternating_line_numbers", "remove_non_formula_lines",
    "non_formula_line_iou_threshold", "figure_table_protection_threshold",
    "skip_formula_offset_calculation", "translate_table_text", "show_char_box",
)
_OCR_FLAGS = (
    "ocr_workaround", "skip_scanned_detection", "disable_rich_text_translate",
    "remove_non_formula_lines",
)


@lru_cache(maxsize=None)
def _schema(cls):
    return {field.name: field.type for field in fields(cls)}


def _restore(cls, value):
    """Decode only the static IR dataclasses; never deserialize executable objects."""
    if value is None:
        return None
    if get_origin(cls) is list:
        return [_restore(get_args(cls)[0], item) for item in value]
    if get_args(cls):  # The IR uses T | None for optional fields.
        return _restore(get_args(cls)[0], value)
    if is_dataclass(cls):
        schema = _schema(cls)
        if value.keys() != schema.keys():
            raise ValueError("Unexpected IR schema")
        return cls(**{key: _restore(schema[key], item) for key, item in value.items()})
    return value


class PreprocessingCache:
    def __init__(self, config):
        self.config = config
        self.path = None
        recovery = getattr(config, "recovery", None)
        if recovery is None or config.debug or config.only_parse_generate_pdf:
            return
        source_key = getattr(config, "preprocessing_source_key", None)
        source_hash = hashlib.sha256(
            orjson.dumps(source_key) if source_key is not None else Path(config.input_file).read_bytes()
        ).hexdigest()
        self.path = recovery.path.parent / f"preprocessing-{source_hash[:16]}.zip"
        settings = {name: getattr(config, name) for name in _SETTINGS}
        model = config.doc_layout_model
        # A caller-supplied layout implementation must not reuse the default model.
        settings["layout_model"] = (
            f"{type(model).__module__}.{type(model).__qualname__}" if model is not None
            else "babeldoc.docvision.doclayout.OnnxModel"
        )
        settings["auto_ocr"] = config.shared_context_cross_split_part.auto_enabled_ocr_workaround
        self.identity = orjson.loads(orjson.dumps(
            [VERSION, pymupdf.VersionBind, source_hash, recovery.fingerprint, settings]
        ))
        context = config.shared_context_cross_split_part
        self.initial_counts = (context.valid_char_count_total, context.total_valid_text_token_count)

    def load(self, prepared_path):
        if self.path is None:
            return None
        self.config.raise_if_cancelled()
        if not self.path.is_file():
            self.config.report_parse_progress("preprocessing_cache_miss", force=True)
            return None
        try:
            with ZipFile(self.path) as archive:
                metadata = orjson.loads(archive.read("metadata.json"))
                if metadata["identity"] != self.identity:
                    self.config.report_parse_progress("preprocessing_cache_miss", force=True)
                    return None
                document = _restore(il_version_1.Document, orjson.loads(archive.read("document.json")))
                pdf_bytes = archive.read("input.pdf")
            media_boxes = {int(key): value for key, value in metadata["media_boxes"].items()}
            flags = metadata["ocr_flags"]
            if set(flags) != set(_OCR_FLAGS) or any(type(value) is not bool for value in flags.values()):
                raise ValueError("Invalid OCR state")
            auto_ocr = metadata["auto_ocr"]
            if type(auto_ocr) is not bool:
                raise ValueError("Invalid shared OCR state")
            counts = metadata["valid_counts"]
            if len(counts) != 2 or any(type(value) is not int or value < 0 for value in counts):
                raise ValueError("Invalid text statistics")
            # Validate before replacing the working input or changing config.
            with pymupdf.open(stream=pdf_bytes, filetype="pdf") as pdf:
                if any(page.page_number < 0 or page.page_number >= len(pdf) for page in document.page):
                    raise ValueError("Checkpoint page count mismatch")
            self.config.raise_if_cancelled()
            Path(prepared_path).write_bytes(pdf_bytes)
            pdf = pymupdf.open(prepared_path)
            try:
                self.config.report_parse_progress("preprocessing_cache_hit", force=True, completedPages=len(document.page))
                for name, value in flags.items():
                    setattr(self.config, name, value)
                context = self.config.shared_context_cross_split_part
                context.auto_enabled_ocr_workaround = auto_ocr
                context.add_valid_counts(*counts)
                logger.info("Reused preprocessing checkpoint (%s pages)", len(document.page))
            except BaseException:
                pdf.close()
                raise
            return document, pdf, media_boxes
        except Exception as error:
            logger.warning("Preprocessing checkpoint unavailable; recomputing: %s", type(error).__name__)
            self.config.report_parse_progress("preprocessing_cache_invalid", force=True)
            return None

    def save(self, document, prepared_path, media_boxes):
        if self.path is None:
            return
        temporary = self.path.with_suffix(".tmp")
        try:
            self.config.raise_if_cancelled()
            metadata = {
                "identity": self.identity,
                "media_boxes": media_boxes,
                "ocr_flags": {name: getattr(self.config, name) for name in _OCR_FLAGS},
                "auto_ocr": self.config.shared_context_cross_split_part.auto_enabled_ocr_workaround,
                "valid_counts": [
                    self.config.shared_context_cross_split_part.valid_char_count_total - self.initial_counts[0],
                    self.config.shared_context_cross_split_part.total_valid_text_token_count - self.initial_counts[1],
                ],
            }
            with ZipFile(temporary, "w", compression=ZIP_DEFLATED, compresslevel=1) as archive:
                archive.writestr("metadata.json", orjson.dumps(metadata, option=orjson.OPT_NON_STR_KEYS))
                archive.writestr("document.json", orjson.dumps(document, default=_orjson_default))
                archive.write(prepared_path, "input.pdf")
            self.config.raise_if_cancelled()
            temporary.replace(self.path)
        except Exception as error:
            # Cache storage must never prevent translation or damage the last checkpoint.
            logger.warning("Unable to save preprocessing checkpoint: %s", type(error).__name__)
            self.config.report_parse_progress("preprocessing_cache_write_failed", force=True)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
