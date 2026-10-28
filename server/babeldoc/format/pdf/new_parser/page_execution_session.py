from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Generic
from typing import TypeVar

from babeldoc.format.pdf.new_parser.bridge_types import EmitterSink
from babeldoc.format.pdf.new_parser.page_interpreter import PageInterpreter
from babeldoc.format.pdf.new_parser.prepared_page import PreparedPdfPage
from babeldoc.format.pdf.new_parser.prepared_page_execution import run_prepared_pages
from babeldoc.format.pdf.new_parser.resource_runtime_types import PageResourceRuntime
from babeldoc.format.pdf.parse_shared import prepare_pdf_for_parse
from babeldoc.format.pdf.translation_config import TranslationConfig

TLoadedPages = TypeVar("TLoadedPages")


@dataclass(slots=True)
class PageExecutionSession(Generic[TLoadedPages]):
    config: TranslationConfig
    sink: EmitterSink
    resource_runtime: PageResourceRuntime
    load_pages: Callable[[str | Path], AbstractContextManager[TLoadedPages]]
    select_prepared_pages: Callable[[TLoadedPages], list[PreparedPdfPage]]
    create_page_interpreter: Callable[
        [PageResourceRuntime, TLoadedPages], PageInterpreter
    ]
    should_translate_page: Callable[[int], bool] | None = None

    def run(self) -> object:
        doc_pdf = None
        try:
            doc_pdf, temp_pdf_path = prepare_pdf_for_parse(
                self.config.input_file, self.config
            )
            self.sink.mupdf = doc_pdf
            with self.load_pages(temp_pdf_path) as loaded_pages:
                page_interpreter = self.create_page_interpreter(
                    self.resource_runtime, loaded_pages
                )
                return run_prepared_pages(
                    self.sink,
                    self.should_translate_page or self.config.should_translate_page,
                    self.select_prepared_pages(loaded_pages),
                    page_interpreter,
                )
        finally:
            if doc_pdf is not None:
                doc_pdf.close()
            self.config.cleanup_temp_files()
