"""Conservative recognition of standalone technical text, never mixed prose."""
import re

from babeldoc.translator.url_utils import STRUCTURAL_TOKEN, is_url_only_text


# Property names, not arbitrary hyphenated English words.
CSS_PROPERTIES = frozenset("""font-family font-size font-weight font-style line-height
letter-spacing text-align text-decoration background-color background-image
margin margin-top margin-right margin-bottom margin-left padding padding-top
padding-right padding-bottom padding-left display position width height color
border border-radius flex-direction align-items justify-content grid-template-columns
""".split())
DOMAIN = r"[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?(?:\.[a-zA-Z0-9-]+)+"
EMAIL = re.compile(r"[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@" + DOMAIN)


def preserved_text_reason(text):
    text = STRUCTURAL_TOKEN.sub("", text or "").strip()
    if not text:
        return None
    if is_url_only_text(text) or re.fullmatch(
        r"www\.[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)*\.[a-zA-Z]{2,}(?:/[^\s<>]*)?", text
    ) or re.fullmatch(r"www [a-z0-9][a-z0-9-]* (?:com|org|net|edu|gov|io|cn)", text):
        return "url_only"
    properties = re.split(r"\s*,\s*", text)
    if all(p in CSS_PROPERTIES for p in properties) and (len(properties) > 1 or "-" in text):
        return "code_only"
    # Only resource attributes: alt/title may contain human-readable prose.
    if re.fullmatch(
        r'''<(?:image|img)\s+(?:["'][^"'<>\n]+["']|(?:src|width|height)\s*=\s*["'][^"'<>\n]+["'])(?:\s+(?:src|width|height)\s*=\s*["'][^"'<>\n]+["'])*\s*/?>''',
        text, re.I,
    ):
        return "code_only"
    addresses = re.split(r"\s*[,;]\s*|\s+", text)
    if any(EMAIL.fullmatch(item) for item in addresses) and all(
        EMAIL.fullmatch(item) or re.fullmatch(r"\.[a-zA-Z]{2,}(?:\.[a-zA-Z]{2,})*", item)
        for item in addresses
    ):
        return "email_only"
    return None
