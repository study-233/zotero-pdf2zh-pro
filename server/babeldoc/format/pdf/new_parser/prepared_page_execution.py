from __future__ import annotations

from collections.abc import Callable

from babeldoc.format.pdf.new_parser.bridge_types import EmitterSink
from babeldoc.format.pdf.new_parser.page_interpreter import PageInterpreter
from babeldoc.format.pdf.new_parser.prepared_page import PreparedPdfPage


def run_prepared_pages(
    sink: EmitterSink,
    should_translate_page: Callable[[int], bool],
    selected_pages: list[PreparedPdfPage],
    page_interpreter: PageInterpreter,
) -> object:
    total_pages = sum(
        1 for page in selected_pages if should_translate_page(page.pageno + 1)
    )
    sink.on_total_pages(total_pages)

    completed = 0
    for page in selected_pages:
        if not should_translate_page(page.pageno + 1):
            continue
        report = getattr(sink, "report_operation", None)
        if report:
            report("page_start", force=True, currentPage=page.pageno + 1,
                   selectedPage=completed + 1, completedPages=completed, totalPages=total_pages)
        page_interpreter.begin_page(page, page.pageno)
        ops_base = page_interpreter.process_page(page)
        sink.on_page_base_operation(ops_base)
        sink.on_page_end()
        page_interpreter.end_page(page, page.pageno)
        completed += 1
        if report:
            report("page_end", force=True, currentPage=page.pageno + 1,
                   selectedPage=completed, completedPages=completed, totalPages=total_pages)

    sink.on_finish()
    return sink.create_il()
