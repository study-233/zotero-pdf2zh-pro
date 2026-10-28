"""Conservative recognition of URL-only text, without network access."""
import re
from urllib.parse import urlsplit


STRUCTURAL_TOKEN = re.compile(
    r"\{\s*v\s*\d+\s*\}|<\s*style\s+id\s*=\s*['\"]\d+['\"]\s*>|<\s*/\s*style\s*>",
    re.I,
)
URL_ITEM = re.compile(
    r"(?:\[\d+\]|\d+[.)]?)?\s*(https?://[^\s<>\{\}\[\]]+)", re.I
)


def is_url_only_text(text: str) -> bool:
    """Accept complete HTTP(S) URLs with optional numeric footnote markers.

    Never remove arbitrary whitespace: it may separate a URL from prose.
    PDF line-wrap reconstruction belongs to the paragraph layout helper.
    """
    text = STRUCTURAL_TOKEN.sub("", text).strip()
    if not text:
        return False
    position = 0
    while position < len(text):
        match = URL_ITEM.match(text, position)
        if match is None:
            return False
        try:
            parsed = urlsplit(match.group(1))
            if not parsed.hostname or parsed.username or parsed.password:
                return False
            # Reject malformed ports as well as malformed IPv6 authorities.
            parsed.port
        except ValueError:
            return False
        position = match.end()
        if position < len(text) and not text[position].isspace():
            return False
        while position < len(text) and text[position].isspace():
            position += 1
    return True
