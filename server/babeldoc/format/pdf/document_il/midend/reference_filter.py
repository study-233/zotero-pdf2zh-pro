from __future__ import annotations

import re
import unicodedata

from babeldoc.format.pdf.document_il import Document
from babeldoc.format.pdf.document_il import PdfParagraph
from babeldoc.format.pdf.document_il import PdfStyle


REFERENCE_LAYOUT_LABELS = {
    "reference",
    "reference_content",
    "reference_hybrid",
}
REFERENCE_HEADINGS = {"references", "bibliography", "参考文献"}
POST_REFERENCE_HEADINGS = {
    "acknowledgment", "acknowledgments", "acknowledgement", "acknowledgements", "致谢", "鸣谢",
    "funding", "funding information", "funding statement", "资助信息", "经费资助",
    "author contributions", "credit authorship contribution statement", "作者贡献",
    "conflict of interest", "conflicts of interest", "conflict of interest statement", "conflicts of interest statement",
    "competing interests", "declaration of competing interest", "利益冲突", "利益冲突声明",
    "data availability", "data availability statement", "数据可用性", "数据可用性声明",
    "declarations", "ethics statement", "ethics approval", "伦理声明",
    "author biography", "author biographies", "作者简介",
}
SECTION_NUMBER = re.compile(
    r"^(?:(?:\d+(?:\.\d+)*|[IVX]+|[一二三四五六七八九十百]+)(?:[.):、]\s*|\s+)"
    r"|第[零〇一二三四五六七八九十百\d]+[章节]\s*|\(\d+\)\s*)",
    re.IGNORECASE,
)
APPENDIX_PREFIX = re.compile(
    r"(?:(?:appendix|appendices|supplement(?:ary|al)?(?:\s+(?:materials?|information))?)\b"
    r"(?:\s+(?:[a-z]|[ivxlcdm]+|\d+)\b)?|附录(?:\s*(?:[a-z]|\d+|[一二三四五六七八九十]+))?)",
    re.IGNORECASE,
)
LETTERED_HEADING = re.compile(r"^[A-Z](?:[.:]\s*|\s+)[A-Z\u4e00-\u9fff]")
TITLE_LABELS = {"title", "paragraph_title", "doc_title", "title_hybrid"}
REFERENCE_SHAPE = re.compile(
    r"(?:^\s*(?:\[\d+\]|\d+[.)])\s+|\b(?:18|19|20)\d{2}\b|\bdoi\s*:|https?://doi\.org/)",
    re.IGNORECASE,
)


def _normalized_heading(text: str) -> str:
    text = " ".join(unicodedata.normalize("NFKC", text).split())
    return SECTION_NUMBER.sub("", text).rstrip(":").rstrip().lower()


def _paragraphs(document: Document) -> list[tuple[int, PdfParagraph]]:
    result: list[tuple[int, PdfParagraph]] = []
    for page_index, page in enumerate(document.page):
        for paragraph in page.pdf_paragraph:
            if paragraph.unicode is not None:
                result.append((page_index, paragraph))
    return result


def _ends_reference_section(paragraph: PdfParagraph, heading: str, reference_style: PdfStyle | None) -> bool:
    if heading in POST_REFERENCE_HEADINGS:
        return True
    # Preserve citation numbering for the broader appendix/title heuristics.
    # A numbered paper title can itself begin with "Supplementary Materials".
    text = " ".join(paragraph.unicode.split())
    if prefix := APPENDIX_PREFIX.match(text):
        subtitle = text[prefix.end():].strip()
        if not subtitle.strip(".:："):
            return True
        if text.endswith((".", "。")):
            return False
        # A cross-reference such as "Appendix A describes ..." is running text.
        return (
            subtitle[0] in ".:：、–—-" or subtitle[0].isupper()
            or "\u4e00" <= subtitle[0] <= "\u9fff" or paragraph.layout_label in TITLE_LABELS
        )

    if not LETTERED_HEADING.match(text) or REFERENCE_SHAPE.search(text) or text.endswith("."):
        return False
    if paragraph.layout_label in TITLE_LABELS:
        return True
    # A heading above an illustration may be classified as its caption. The
    # reference heading provides a sample of this document's section typography.
    style = paragraph.pdf_style
    return bool(
        style and reference_style and style.font_id and style.font_id == reference_style.font_id
        and style.font_size and reference_style.font_size
        and abs(style.font_size - reference_style.font_size) < 0.1
    )


def _has_reference_entries(
    following: list[tuple[int, PdfParagraph]], reference_style: PdfStyle | None, likely_location: bool,
) -> bool:
    entries = 0
    for _, paragraph in following:
        heading = _normalized_heading(paragraph.unicode)
        if heading in REFERENCE_HEADINGS:
            continue  # The next page may repeat the bibliography heading.
        if _ends_reference_section(paragraph, heading, reference_style):
            break
        if (paragraph.layout_label or "").lower() in REFERENCE_LAYOUT_LABELS:
            likely_location = True
        if REFERENCE_SHAPE.search(paragraph.unicode):
            entries += 1
        if likely_location and entries >= 2:
            return True
    return False


def find_reference_paragraph_ids(document: Document) -> set[int]:
    """Return conservative paragraph identities that should remain untranslated."""

    paragraphs = _paragraphs(document)
    skipped = set()
    halfway_page = len(document.page) // 2
    section = None
    reference_style = None
    for index, (page_index, paragraph) in enumerate(paragraphs):
        heading = _normalized_heading(paragraph.unicode)
        if heading in REFERENCE_HEADINGS:
            following = paragraphs[index + 1 : index + 9]
            # A long appendix may put the bibliography in the first half. Require
            # corroboration from the layout model there to avoid a contents page.
            likely_location = page_index >= halfway_page or section == "after_references"
            if _has_reference_entries(following, paragraph.pdf_style, likely_location):
                section = "references"
                reference_style = paragraph.pdf_style
        elif skipped and section != "after_references" and _ends_reference_section(paragraph, heading, reference_style):
            section = "after_references"

        labelled_reference = (paragraph.layout_label or "").lower() in REFERENCE_LAYOUT_LABELS
        if section == "references" or (labelled_reference and section != "after_references"):
            skipped.add(id(paragraph))
    return skipped
