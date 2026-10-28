from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from babeldoc.format.pdf.new_parser.active_font_resource_runtime import (
    create_active_font_resource_runtime,
)
from babeldoc.format.pdf.new_parser.bridge_types import EmitterSink
from babeldoc.format.pdf.new_parser.native_page_interpreter import (
    create_native_page_interpreter,
)
from babeldoc.format.pdf.new_parser.page_execution_session import PageExecutionSession
from babeldoc.format.pdf.new_parser.pymupdf_prepared_page_access import (
    load_prepared_pdf_pages,
)
from babeldoc.format.pdf.new_parser.resource_runtime_types import PageResourceRuntime
from babeldoc.format.pdf.translation_config import TranslationConfig


@dataclass(slots=True)
class NativePageExecutionSession:
    config: TranslationConfig
    sink: EmitterSink
    create_text_run_positioner: Callable[[PageResourceRuntime], object]
    create_resource_runtime: Callable[[], PageResourceRuntime] = (
        create_active_font_resource_runtime
    )

    def run(self) -> object:
        resource_runtime = self.create_resource_runtime()
        return PageExecutionSession(
            config=self.config,
            sink=self.sink,
            resource_runtime=resource_runtime,
            load_pages=lambda temp_pdf_path: load_prepared_pdf_pages(
                temp_pdf_path,
                should_include_page=self.config.should_translate_page,
            ),
            select_prepared_pages=lambda prepared_pages: prepared_pages,
            create_page_interpreter=lambda resource_runtime, _prepared_pages: (
                create_native_page_interpreter(
                    self.sink,
                    self.create_text_run_positioner(resource_runtime),
                    resource_runtime,
                    config=self.config,
                )
            ),
        ).run()
