"""Translate text slots while the application owns the PDF's structural tokens."""
import json
import re

from pdf2zh_pro.translator.validation import (
    PLACEHOLDER_PATTERN, InvalidTranslation, clean_json, validate_text,
)


class PlaceholderRepair:
    """A bounded fallback, never a heuristic that appends missing formulas."""

    def __init__(self, source, original_prompt, lang_out, same_text_check=True):
        self.source = source
        self.lang_out = lang_out
        self.same_text_check = same_text_check
        self.parts = []
        self.slots = {}
        position = 0
        for match in PLACEHOLDER_PATTERN.finditer(source):
            self._add_text(source[position:match.start()])
            self.parts.append(match.group())
            position = match.end()
        self._add_text(source[position:])
        self.prompt = (
            f"Translate the text segments below into {lang_out}. A previous translation failed "
            "to preserve PDF placeholders. The application now retains ALL formula placeholders, "
            "citation markers and style boundaries and will insert them between your segments.\n"
            "The original request is read-only context for meaning, terminology and instructions; "
            "its output format is superseded by the JSON format below.\n"
            "Translate every segment completely, using the whole original paragraph as context. "
            "Segments may be sentence or word fragments. Keep each segment's meaning at its own ID; "
            "do not merge, omit, repeat or move text between segments. Preserve mathematical "
            "symbols and proper names where appropriate. Never output placeholders or style tags.\n"
            'Return ONLY a JSON object {"segments":[{"id":0,"output":"translated text"}, ...]} '
            "with exactly one nonempty output for every supplied ID. No explanations.\n\n"
            + json.dumps({"read_only_original_request": original_prompt,
                          "segments": [{"id": key, "input": value.strip()}
                                       for key, value in self.slots.items()]}, ensure_ascii=False)
        )

    def _add_text(self, text):
        # Whitespace, punctuation and standalone numbers do not need generation.
        if re.search(r"[^\W\d_]", text, re.UNICODE):
            key = len(self.slots)
            self.slots[key] = text
            self.parts.append(key)
        else:
            self.parts.append(text)

    def restore(self, output):
        try:
            data = json.loads(clean_json(output))
        except (ValueError, TypeError, AttributeError) as error:
            raise InvalidTranslation("invalid_json") from error
        if not isinstance(data, dict) or not isinstance(data.get("segments"), list):
            raise InvalidTranslation("invalid_paragraph")
        translated = {}
        for row in data["segments"]:
            if not isinstance(row, dict):
                raise InvalidTranslation("invalid_paragraph")
            key = row.get("id")
            if type(key) is not int or key not in self.slots or key in translated:
                raise InvalidTranslation("invalid_or_duplicate_paragraph_id")
            value = row.get("output")
            source = self.slots[key]
            validate_text(source, value, self.lang_out, self.same_text_check)
            # Preserve boundary whitespace independently of model normalization.
            leading = source[:len(source) - len(source.lstrip())]
            trailing = source[len(source.rstrip()):]
            translated[key] = leading + value.strip() + trailing
        if translated.keys() != self.slots.keys():
            raise InvalidTranslation("paragraph_count_mismatch")
        result = "".join(translated[part] if type(part) is int else part for part in self.parts)
        validate_text(self.source, result, self.lang_out, self.same_text_check)
        return result
