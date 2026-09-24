"""Hide document markup from LLMs while retaining the original layout tokens."""
import re
import logging
from collections import defaultdict, deque

logger = logging.getLogger(__name__)

from babeldoc.translator.validation import InvalidTranslation

TOKEN = re.compile(r"\{v\d+\}")
TAG = re.compile(r'''(?i:<code(?:\s[^<>]*)?>[\s\S]*?</code\s*>)|</?[A-Za-z][\w:-]*(?:\s+(?:[^<>"']|"[^"<>]*"|'[^'<>]*')*)?\s*/?>''')


class LiteralTags:
    def __init__(self, source, formula_text=None):
        # Project formula glyphs back to text only to locate tag boundaries.
        # Each projected character keeps its exact span in the prepared input.
        formula_text = formula_text or {}
        projected, spans = [], []
        position = 0
        for match in TOKEN.finditer(source):
            for index in range(position, match.start()):
                projected.append(source[index])
                spans.append((index, index + 1))
            value = formula_text.get(match.group(), match.group())
            projected.extend(value)
            spans.extend([(match.start(), match.end())] * len(value))
            position = match.end()
        for index in range(position, len(source)):
            projected.append(source[index])
            spans.append((index, index + 1))
        self.mapping = {}
        self.source = source
        self.text = source
        next_id = max([int(t[2:-1]) for t in TOKEN.findall(source)] + [0]) + 1
        replacements = []
        for match in TAG.finditer("".join(projected)):
            # These are the translation engine's rich-text boundaries.
            if re.match(r"</?style(?:\s|>)", match.group(), re.I):
                continue
            start, end = spans[match.start()][0], spans[match.end() - 1][1]
            # Never consume only part of a formula or another layout token.
            if ((match.start() and spans[match.start() - 1] == spans[match.start()])
                    or (match.end() < len(spans) and spans[match.end()] == spans[match.end() - 1])):
                continue
            token = f"{{v{next_id}}}"
            next_id += 1
            self.mapping[token] = source[start:end]
            replacements.append((start, end, token))
        for start, end, token in reversed(replacements):
            self.text = self.text[:start] + token + self.text[end:]

    def protect_existing(self, output):
        """Rebind a previously validated layout output to this input's tag markers."""
        if not self.mapping:
            return output
        tokens = defaultdict(deque)
        for token, fragment in self.mapping.items():
            tokens[fragment].append(token)
        pattern = re.compile("|".join(re.escape(s) for s in sorted(tokens, key=len, reverse=True)))
        def replace(match):
            queue = tokens[match.group()]
            if not queue:
                raise InvalidTranslation("placeholder_mismatch")
            return queue.popleft()
        protected = pattern.sub(replace, output)
        self.restore(protected)
        return protected

    def restore(self, output):
        expected = list(self.mapping)
        actual = [token for token in TOKEN.findall(output) if token in self.mapping]
        if actual != expected:
            logger.warning("document tag markers differ: expected=%s actual=%s", expected, actual)
            raise InvalidTranslation("placeholder_mismatch")
        return TOKEN.sub(lambda m: self.mapping.get(m.group(), m.group()), output)
