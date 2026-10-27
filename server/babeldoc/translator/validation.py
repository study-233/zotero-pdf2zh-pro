"""Validation shared by cached, batch and fallback translations."""
import json
import re
from collections import Counter
from dataclasses import dataclass
from babeldoc.translator.preserved_text import preserved_text_reason
import logging
from diagnostic_contract import CONTRACT

logger = logging.getLogger(__name__)

PLACEHOLDER_PATTERN = re.compile(
    r"\{\s*v\s*\d+\s*\}|<\s*style\s+id\s*=\s*['\"]\d+['\"]\s*>|<\s*/\s*style\s*>", re.I,
)


def normalize_placeholder(token):
    return re.sub(r"\s+", "", token).replace('"', "'").lower()


def canonicalize_placeholders(source, output):
    """Use exact source spellings after validation accepts whitespace/quote variants."""
    originals = {normalize_placeholder(m.group()): m.group() for m in PLACEHOLDER_PATTERN.finditer(source)}
    return PLACEHOLDER_PATTERN.sub(lambda m: originals.get(normalize_placeholder(m.group()), m.group()), output)


def _placeholder_structure(text):
    stack, positions = [], Counter()
    for match in PLACEHOLDER_PATTERN.finditer(text):
        token = normalize_placeholder(match.group())
        if token == "</style>":
            if not stack:
                return None
            stack.pop()
        else:
            positions[(token, tuple(stack))] += 1
            if token.startswith("<style"):
                stack.append(token)
    return None if stack else positions


class InvalidTranslation(ValueError):
    def __init__(self, code, *, missing=0, extra=0):
        super().__init__(code)
        self.code = code if isinstance(code, str) and code in CONTRACT['validationErrors'] else 'unknown_validation'
        self.diagnostic_fields = {
            'missingPlaceholderCount': missing,
            'extraPlaceholderCount': extra,
        } if self.code == 'placeholder_mismatch' else {}


def clean_json(text):
    text = text.strip()
    if text.startswith("```") and text.endswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)[:-3].strip()
    return text


def validate_text(source, output, lang_out="", same_text_check=True):
    if not isinstance(output, str) or not output.strip():
        raise InvalidTranslation("empty_translation")
    # Formula tokens and style boundaries are structural, including multiplicity.
    pattern = PLACEHOLDER_PATTERN.pattern
    tokens = lambda s: Counter(normalize_placeholder(x) for x in re.findall(pattern, s, re.I))
    if tokens(source) != tokens(output):
        missing = sum((tokens(source) - tokens(output)).values())
        extra = sum((tokens(output) - tokens(source)).values())
        logger.warning("translation placeholders differ", extra={'diagnostic': {
            'validationCode': 'placeholder_mismatch', 'missingPlaceholderCount': missing,
            'extraPlaceholderCount': extra}})
        raise InvalidTranslation("placeholder_mismatch", missing=missing, extra=extra)
    # Matching counts alone can still put a formula inside a style run, where
    # the layout parser would consume it as text, or accept unbalanced tags.
    structure = _placeholder_structure(source)
    if structure is not None and structure != _placeholder_structure(output):
        raise InvalidTranslation("placeholder_mismatch")
    plain = lambda s: re.sub(pattern, "", s, flags=re.I).strip()
    src, dst = plain(source), plain(output)
    if src == dst and preserved_text_reason(src):
        return
    words = re.findall(r"[A-Za-z]+", src)
    prose = sum(w.islower() and len(w) > 2 for w in words) >= 3
    heading = src.lower() in {"abstract", "introduction", "discussion", "conclusion", "conclusions", "references", "acknowledgments", "acknowledgements"}
    if same_text_check and (prose or heading):
        if re.sub(r"\W", "", src).lower() == re.sub(r"\W", "", dst).lower():
            raise InvalidTranslation("unchanged_translation")
        if lang_out.lower().startswith("zh") and not re.search(r"[\u3400-\u9fff]", dst):
            raise InvalidTranslation("target_language_missing")


@dataclass
class BatchValidationResult:
    valid_outputs: dict[int, str]
    invalid_outputs: dict[int, str]
    unassigned_rows: bool = False

    @property
    def complete(self):
        return not self.invalid_outputs and not self.unassigned_rows


def inspect_batch(output, sources, lang_out="", same_text_check=True):
    try:
        rows = json.loads(clean_json(output))
    except (ValueError, TypeError, AttributeError) as exc:
        raise InvalidTranslation("invalid_json") from exc
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list):
        raise InvalidTranslation("paragraph_count_mismatch")
    results = BatchValidationResult({}, {})
    by_id = {}
    for row in rows:
        if not isinstance(row, dict):
            results.unassigned_rows = True
            continue
        key = row.get("id")
        if isinstance(key, str) and key.isdecimal():
            key = int(key)
        if type(key) is not int or key not in range(len(sources)):
            results.unassigned_rows = True
            continue
        by_id.setdefault(key, []).append(row.get("output"))
    for key, source in enumerate(sources):
        values = by_id.get(key, [])
        if not values:
            results.invalid_outputs[key] = "paragraph_count_mismatch"
            continue
        if len(values) != 1:
            results.invalid_outputs[key] = "invalid_or_duplicate_paragraph_id"
            continue
        value = values[0]
        try:
            validate_text(source, value, lang_out, same_text_check)
        except InvalidTranslation as error:
            results.invalid_outputs[key] = str(error)
        else:
            results.valid_outputs[key] = value
    return results


def validate_batch(output, sources, lang_out="", same_text_check=True):
    result = inspect_batch(output, sources, lang_out, same_text_check)
    if result.unassigned_rows:
        raise InvalidTranslation("invalid_paragraph")
    if result.invalid_outputs:
        raise InvalidTranslation(next(iter(result.invalid_outputs.values())))
    return result.valid_outputs
