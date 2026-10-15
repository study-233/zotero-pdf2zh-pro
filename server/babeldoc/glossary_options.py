"""Validate task-owned glossary data without accessing client filesystem paths."""
import re
from functools import lru_cache

import regex

GLOSSARY_POLICY_VERSION = 2


def glossary_text(text):
    text = re.sub(r"</?style\b[^>]*>", "", text, flags=re.I)
    text = re.sub(r"\{\s*v\s*\d+\s*\}", " ", text, flags=re.I)
    return " ".join(text.split())


@lru_cache(maxsize=4096)
def _term_pattern(source):
    # Latin words/abbreviations must not match inside another word. CJK
    # terms still match in unsegmented text, including adjacent Latin terms.
    word = r"[\p{Latin}\p{N}_]"
    start = f"(?<!{word})" if regex.fullmatch(word, source[0]) else ""
    end = f"(?!{word})" if regex.fullmatch(word, source[-1]) else ""
    return regex.compile(start + regex.escape(source) + end, regex.I | regex.V0)


def contains_glossary_term(source, text):
    source = glossary_text(source)
    return bool(source and _term_pattern(source).search(glossary_text(text)))


def matching_glossary_entries(entries, language, text):
    return [row for row in glossary_entries_for_language(entries, language)
            if contains_glossary_term(row["source"], text)]


def normalize_glossary_entries(value):
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("glossaryEntries must be an array")
    entries, seen = [], {}
    for index, row in enumerate(value, 1):
        if not isinstance(row, dict):
            raise ValueError(f"glossaryEntries row {index}: expected an object")
        source, target, language = (row.get("source"), row.get("target"), row.get("tgt_lng", ""))
        if not isinstance(source, str) or not source.strip() or not isinstance(target, str) or not target.strip():
            raise ValueError(f"glossaryEntries row {index}: source and target must be nonempty strings")
        if not isinstance(language, str):
            raise ValueError(f"glossaryEntries row {index}: tgt_lng must be a string")
        source, target = source.strip(), target.strip()
        language = language.strip().lower().replace("_", "-")
        key = (re.sub(r"\s+", " ", source).lower(), language)
        previous = seen.get(key)
        if previous is not None:
            if previous[1] != target:
                raise ValueError(f"glossaryEntries rows {previous[0]} and {index}: conflicting targets")
            continue
        seen[key] = (index, target)
        entries.append({"source": source, "target": target, "tgt_lng": language})
    return entries


def glossary_entries_for_language(entries, language):
    """An explicitly language-scoped entry overrides a language-neutral entry."""
    language = language.lower().replace("_", "-")
    selected = {}
    for row in normalize_glossary_entries(entries):
        if row["tgt_lng"] not in ("", language):
            continue
        key = re.sub(r"\s+", " ", row["source"]).lower()
        if key not in selected or row["tgt_lng"] == language:
            selected[key] = row
    return sorted(selected.values(), key=lambda row: (-len(row["source"]), row["source"].casefold()))
