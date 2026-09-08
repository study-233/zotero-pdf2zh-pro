import logging
import re

from babeldoc.format.pdf.document_il import il_version_1
from babeldoc.translator.url_utils import is_url_only_text

logger = logging.getLogger(__name__)


def is_url_only_paragraph(paragraph) -> bool:
    source = paragraph.unicode or ""
    if is_url_only_text(source):
        return True
    if "http" not in source.lower() or paragraph.box is None:
        return False
    chars = []
    for comp in paragraph.pdf_paragraph_composition:
        group = comp.pdf_line or comp.pdf_same_style_characters or comp.pdf_formula
        if group is not None:
            chars.extend(group.pdf_character)
        elif comp.pdf_character is not None:
            chars.append(comp.pdf_character)
        else:
            # Unicode-only compositions lack the geometry needed to prove a wrap.
            return False
    if re.sub(r"\s", "", "".join(c.char_unicode or "" for c in chars)) != re.sub(r"\s", "", source):
        return False
    lines = []
    previous = None
    for char in chars:
        text, box = char.char_unicode or "", char.box
        if not text.strip():
            if lines:
                lines[-1][0] += text
            continue
        if box is None or box.y2 <= box.y:
            return False
        height = box.y2 - box.y
        new_line = previous is not None and (
            box.x < previous.x - height / 2
            and (box.y + box.y2) / 2 < (previous.y + previous.y2) / 2 - height / 2
        )
        if not lines or new_line:
            lines.append([text, box, box])
        else:
            # Preserve spaces implied by glyph positions, even if extraction omitted them.
            if previous is not None and box.x - previous.x2 > height * 0.18:
                lines[-1][0] += " "
            lines[-1][0] += text
            lines[-1][2] = box
        previous = box
    joined = ""
    previous_line = None
    for text, first, last in lines:
        text = text.strip()
        if is_url_only_text(text):
            joined += (" " if joined else "") + text
        elif previous_line is not None:
            previous_text, previous_last = previous_line
            height = first.y2 - first.y
            # Only a single lowercase URI-path fragment, wrapped after '/' at the
            # paragraph's right edge, may continue the preceding URL line.
            if not (
                is_url_only_text(joined)
                and previous_text.rstrip().endswith("/")
                and re.fullmatch(r"[a-z0-9][a-z0-9._~!$&'()*+,;=:@%/?#-]*", text)
                and abs(previous_last.x2 - paragraph.box.x2) <= height
                and abs(first.x - paragraph.box.x) <= height
                and 0 < previous_last.y - first.y <= height * 2
                and 0.8 <= (previous_last.y2 - previous_last.y) / height <= 1.2
            ):
                return False
            joined += text
        else:
            return False
        previous_line = (text, last)
    return is_url_only_text(joined)


def is_cid_paragraph(paragraph: il_version_1.PdfParagraph):
    chars: list[il_version_1.PdfCharacter] = []
    for composition in paragraph.pdf_paragraph_composition:
        if composition.pdf_line:
            chars.extend(composition.pdf_line.pdf_character)
        elif composition.pdf_same_style_characters:
            chars.extend(composition.pdf_same_style_characters.pdf_character)
        elif composition.pdf_same_style_unicode_characters:
            continue
        #     chars.extend(composition.pdf_same_style_unicode_characters.unicode)
        elif composition.pdf_formula:
            chars.extend(composition.pdf_formula.pdf_character)
        elif composition.pdf_character:
            chars.append(composition.pdf_character)
        else:
            logger.error(
                f"Unknown composition type. "
                f"Composition: {composition}. "
                f"Paragraph: {paragraph}. ",
            )
            continue

    cid_count = 0
    for char in chars:
        if re.match(r"^\(cid:\d+\)$", char.char_unicode):
            cid_count += 1

    return cid_count > len(chars) * 0.8


NUMERIC_PATTERN = re.compile(r"^-?\d+(\.\d+)?$")


def is_pure_numeric_paragraph(paragraph) -> bool:
    """只检查段落是否为纯数字（支持整数、小数、负数）"""

    if not paragraph or not getattr(paragraph, "unicode", None):
        return False

    text = paragraph.unicode.strip()
    if not text:
        return False

    return bool(NUMERIC_PATTERN.match(text))


def is_placeholder_only_paragraph(paragraph: il_version_1.PdfParagraph) -> bool:
    """Check if a paragraph contains only placeholders and whitespace.

    Args:
        paragraph: PDF paragraph to check

    Returns:
        True if the paragraph contains only placeholders (formula or style tags)
        and whitespace, False otherwise
    """
    if not paragraph or not paragraph.unicode:
        return False

    for composition in paragraph.pdf_paragraph_composition:
        if composition.pdf_formula:
            # Formula composition is allowed
            continue
        elif composition.pdf_character:
            # Check if single character is whitespace
            if not composition.pdf_character.char_unicode.isspace():
                return False
        elif composition.pdf_line:
            # Check if all characters in the line are whitespace
            for char in composition.pdf_line.pdf_character:
                if not char.char_unicode.isspace():
                    return False
        elif composition.pdf_same_style_characters:
            # Check if all characters in the group are whitespace
            for char in composition.pdf_same_style_characters.pdf_character:
                if not char.char_unicode.isspace():
                    return False
        elif composition.pdf_same_style_unicode_characters:
            # Check if the unicode content is only whitespace
            if not composition.pdf_same_style_unicode_characters.unicode.isspace():
                return False
        else:
            # Unknown composition type, conservatively return False
            return False

    return True
