"""Structured exports of an existing PDF, independent of translation providers."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path


def export_document(input_path: Path, output_path: Path, check_cancelled=lambda: None, *, format):
    """Write the selected UTF-8 Markdown, layout JSON or plain text file."""
    import pymupdf

    if format not in {"md", "json", "txt"}:
        raise ValueError("Unsupported document export format")
    check_cancelled()
    try:
        document = pymupdf.open(input_path)
    except pymupdf.FileDataError:
        return {"code": "export_invalid_pdf"}
    with document:
        if not document.is_pdf or document.needs_pass:
            return {"code": "export_invalid_pdf"}
        options = {"use_ocr": False, "show_progress": False}
        # Use the public Document API. Layout is never enabled in the PDF
        # translation process: this runs in its own supervised worker.
        text = document.to_text(**options)
        check_cancelled()
        if not text.strip():
            return {"code": "export_needs_ocr"}
        content = text
        if format == "md":
            content = document.to_markdown(embed_images=True, **options)
        elif format == "json":
            structure = json.loads(document.to_json(**options))
            structure["filename"] = input_path.name
            content = json.dumps(structure, ensure_ascii=False, indent=2)
        page_count = len(document)
    check_cancelled()
    output_path.write_text(content, encoding="utf-8")
    return {"pages": page_count}


def export_worker(payload, cancel, emit):
    def check_cancelled():
        if cancel.is_set():
            raise asyncio.CancelledError

    return export_document(Path(payload["input_path"]), Path(payload["output_path"]), check_cancelled,
                           format=payload["format"])
