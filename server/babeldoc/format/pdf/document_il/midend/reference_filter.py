from __future__ import annotations

import re

from babeldoc.format.pdf.document_il import Document
from babeldoc.format.pdf.document_il import PdfParagraph


REFERENCE_LAYOUT_LABELS = {
    "reference",
    "reference_content",
    "reference_hybrid",
}
REFERENCE_HEADINGS = {"references", "bibliography", "参考文献"}
END_HEADINGS = re.compile(
    r"^(appendix(?:\s+[a-z0-9]+)?|supplement(?:ary)?(?:\s+materials?)?|附录)\s*[:：]?$",
    re.IGNORECASE,
)
REFERENCE_SHAPE = re.compile(
    r"(?:^\s*(?:\[\d+\]|\d+[.)])\s+|\b(?:18|19|20)\d{2}\b|\bdoi\s*:|https?://doi\.org/)",
    re.IGNORECASE,
)


def _normalized_heading(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().rstrip(":：").lower()


def _paragraphs(document: Document) -> list[tuple[int, PdfParagraph]]:
    result: list[tuple[int, PdfParagraph]] = []
    for page_index, page in enumerate(document.page):
        for paragraph in page.pdf_paragraph:
            if paragraph.unicode is not None:
                result.append((page_index, paragraph))
    return result


def find_reference_paragraph_ids(document: Document) -> set[int]:
    """Return conservative paragraph identities that should remain untranslated."""

    paragraphs = _paragraphs(document)
    skipped = {
        id(paragraph)
        for _, paragraph in paragraphs
        if str(paragraph.layout_label or "").lower() in REFERENCE_LAYOUT_LABELS
    }
    if not paragraphs or not document.page:
        return skipped

    halfway_page = len(document.page) / 2
    fallback_start: int | None = None
    for index, (page_index, paragraph) in enumerate(paragraphs):
        if page_index < halfway_page:
            continue
        heading = _normalized_heading(paragraph.unicode or "")
        if heading not in REFERENCE_HEADINGS:
            continue
        following = [
            candidate.unicode or ""
            for _, candidate in paragraphs[index + 1 : index + 9]
            if (candidate.unicode or "").strip()
        ]
        if sum(bool(REFERENCE_SHAPE.search(text)) for text in following) >= 2:
            fallback_start = index
            break

    if fallback_start is None:
        return skipped

    for _, paragraph in paragraphs[fallback_start:]:
        text = (paragraph.unicode or "").strip()
        if id(paragraph) not in skipped and END_HEADINGS.match(text):
            break
        skipped.add(id(paragraph))
    return skipped
