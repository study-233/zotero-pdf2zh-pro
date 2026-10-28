from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from babeldoc.format.pdf.document_il.frontend.il_creater_active import ActiveILCreater
from babeldoc.format.pdf.parse_shared import build_parse_only_config
from babeldoc.format.pdf.translation_config import TranslationConfig


def run_active_parse_session(
    pdf_path: str | Path,
    *,
    pages: str | None = None,
    working_dir: str | Path | None = None,
    debug: bool = False,
    create_session: Callable[[TranslationConfig, ActiveILCreater], object],
):
    pdf_path = Path(pdf_path)
    config = build_parse_only_config(
        pdf_path,
        pages=pages,
        working_dir=working_dir,
        debug=debug,
    )
    sink = ActiveILCreater(config)
    return create_session(config, sink).run()
