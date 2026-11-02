"""Translate text slots while the application owns the PDF's structural tokens."""
import json
import re

from pdf2zh_pro.translator.validation import (
    PLACEHOLDER_PATTERN, InvalidTranslation, clean_json, punctuation_only, validate_text,
)


class PlaceholderRepair:
    """A bounded fallback, never a heuristic that appends missing formulas."""

    def __init__(self, source, original_prompt, lang_out, same_text_check=True):
        self.source = source
        self.lang_out = lang_out
        self.same_text_check = same_text_check
        self.parts = []
        self.slots = {}
        self.punctuation_slots = set()
        position = 0
        in_style = False
        for match in PLACEHOLDER_PATTERN.finditer(source):
            fragment = source[position:match.start()]
            self._add_text(fragment, styled_punctuation=in_style and punctuation_only(fragment))
            self.parts.append(match.group())
            in_style = match.group().lstrip().lower().startswith("<style")
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
            "with exactly one output for every supplied ID. All text outputs must be nonempty. "
            "For punctuation-only IDs, return the target punctuation, or an empty string if it "
            "is unnecessary. Never put words in those IDs. No explanations.\n\n"
            + json.dumps({"read_only_original_request": original_prompt,
                          "segments": [{"id": key, "input": value.strip()}
                                       for key, value in self.slots.items()]}, ensure_ascii=False)
        )

    def _add_text(self, text, *, styled_punctuation=False):
        if styled_punctuation or re.search(r"[^\W\d_]", text, re.UNICODE):
            key = len(self.slots)
            self.slots[key] = text
            self.parts.append(key)
            if styled_punctuation:
                self.punctuation_slots.add(key)
        else:
            self.parts.append(text)

    def restore(self, output):
        try:
            data = json.loads(clean_json(output))
        except (ValueError, TypeError, AttributeError) as error:
            raise InvalidTranslation("invalid_json") from error
        if not isinstance(data, dict) or not isinstance(data.get("segments"), list):
            raise InvalidTranslation("invalid_paragraph")
        def segment_error(code, key=None):
            error = InvalidTranslation(code)
            error.diagnostic_fields.update(expectedSegmentCount=len(self.slots),
                                           receivedSegmentCount=len(data["segments"]))
            if type(key) is int and key in self.slots:
                error.diagnostic_fields["invalidSegmentId"] = key
            return error

        translated = {}
        for row in data["segments"]:
            if not isinstance(row, dict):
                raise segment_error("invalid_paragraph")
            key = row.get("id")
            if type(key) is not int or key not in self.slots or key in translated:
                raise segment_error("invalid_or_duplicate_paragraph_id", key)
            value = row.get("output")
            if not isinstance(value, str) or (not value.strip() and key not in self.punctuation_slots):
                raise segment_error("empty_repair_segment", key)
            source = self.slots[key]
            if key in self.punctuation_slots:
                if value.strip() and not punctuation_only(value):
                    raise segment_error("placeholder_mismatch", key)
            else:
                validate_text(source, value, self.lang_out, self.same_text_check)
            # Preserve boundary whitespace independently of model normalization.
            leading = source[:len(source) - len(source.lstrip())]
            trailing = source[len(source.rstrip()):]
            translated[key] = leading + value.strip() + trailing
        if translated.keys() != self.slots.keys():
            raise segment_error("missing_repair_segment", min(self.slots.keys() - translated.keys()))
        result = "".join(translated[part] if type(part) is int else part for part in self.parts)
        validate_text(self.source, result, self.lang_out, self.same_text_check)
        return result
