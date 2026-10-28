from __future__ import annotations

import shutil
from pathlib import Path

import pymupdf

from babeldoc.format.pdf.high_level import fix_filter
from babeldoc.format.pdf.high_level import fix_media_box
from babeldoc.format.pdf.high_level import fix_null_page_content
from babeldoc.format.pdf.high_level import fix_null_xref
from babeldoc.format.pdf.high_level import open_pdf_with_save_fallback
from babeldoc.format.pdf.high_level import save_pdf_with_same_path_fallback
from babeldoc.format.pdf.translation_config import TranslationConfig
from babeldoc.progress_monitor import ProgressMonitor


class _ParseOnlyDocLayoutModel:
    """Placeholder object to avoid loading layout models in parse-only tooling."""


PARSE_PDF_STAGE_NAME = "Parse PDF and Create Intermediate Representation"


def build_parse_only_config(
    pdf_path: str | Path,
    *,
    pages: str | None = None,
    working_dir: str | Path | None = None,
    debug: bool = False,
) -> TranslationConfig:
    return TranslationConfig(
        translator=None,
        input_file=pdf_path,
        lang_in="",
        lang_out="",
        doc_layout_model=_ParseOnlyDocLayoutModel(),
        pages=pages,
        debug=debug,
        working_dir=working_dir,
        progress_monitor=ProgressMonitor([(PARSE_PDF_STAGE_NAME, 1.0)]),
        auto_extract_glossary=False,
        skip_translation=True,
        table_model=None,
    )


def prepare_pdf_for_parse(
    pdf_path: str | Path,
    config: TranslationConfig,
) -> tuple[pymupdf.Document, Path]:
    pdf_path = Path(pdf_path)
    temp_pdf_path = config.get_working_file_path("input.pdf")
    shutil.copy2(pdf_path, temp_pdf_path)

    doc_pdf = open_pdf_with_save_fallback(pdf_path, temp_pdf_path)

    fix_null_page_content(doc_pdf)
    fix_filter(doc_pdf)
    fix_null_xref(doc_pdf)
    fix_media_box(doc_pdf)
    doc_pdf = save_pdf_with_same_path_fallback(doc_pdf, temp_pdf_path)
    return doc_pdf, temp_pdf_path
