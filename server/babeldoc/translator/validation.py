"""Validation shared by cached, batch and fallback translations."""
import json
import re
from collections import Counter
from babeldoc.translator.url_utils import is_url_only_text


class InvalidTranslation(ValueError):
    pass


def clean_json(text):
    text = text.strip()
    if text.startswith("```") and text.endswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)[:-3].strip()
    return text


def validate_text(source, output, lang_out="", same_text_check=True):
    if not isinstance(output, str) or not output.strip():
        raise InvalidTranslation("empty_translation")
    # Formula tokens and style boundaries are structural, including multiplicity.
    pattern = r"\{\s*v\s*\d+\s*\}|<\s*style\s+id\s*=\s*['\"]\d+['\"]\s*>|<\s*/\s*style\s*>"
    normalize = lambda s: re.sub(r"\s+", "", s).replace('"', "'").lower()
    tokens = lambda s: Counter(normalize(x) for x in re.findall(pattern, s, re.I))
    if tokens(source) != tokens(output):
        raise InvalidTranslation("placeholder_mismatch")
    plain = lambda s: re.sub(pattern, "", s, flags=re.I).strip()
    src, dst = plain(source), plain(output)
    if src == dst and is_url_only_text(src):
        return
    words = re.findall(r"[A-Za-z]+", src)
    prose = sum(w.islower() and len(w) > 2 for w in words) >= 3
    heading = src.lower() in {"abstract", "introduction", "discussion", "conclusion", "conclusions", "references", "acknowledgments", "acknowledgements"}
    if same_text_check and (prose or heading):
        if re.sub(r"\W", "", src).lower() == re.sub(r"\W", "", dst).lower():
            raise InvalidTranslation("unchanged_translation")
        if lang_out.lower().startswith("zh") and not re.search(r"[\u3400-\u9fff]", dst):
            raise InvalidTranslation("target_language_missing")


def validate_batch(output, sources, lang_out="", same_text_check=True):
    try:
        rows = json.loads(clean_json(output))
    except (ValueError, TypeError, AttributeError) as exc:
        raise InvalidTranslation("invalid_json") from exc
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list) or len(rows) != len(sources):
        raise InvalidTranslation("paragraph_count_mismatch")
    results = {}
    for row in rows:
        if not isinstance(row, dict):
            raise InvalidTranslation("invalid_paragraph")
        key = row.get("id")
        if isinstance(key, str) and key.isdecimal():
            key = int(key)
        if type(key) is not int or key not in range(len(sources)) or key in results:
            raise InvalidTranslation("invalid_or_duplicate_paragraph_id")
        value = row.get("output")
        validate_text(sources[key], value, lang_out, same_text_check)
        results[key] = value
    return results
