"""Deterministic prose-only input repairs, without changing PDF fragment ownership."""

import re
from dataclasses import dataclass
from itertools import chain
from types import MappingProxyType


BODY_INPUT_VERSION = "body-input-v1"
BODY_LABELS = frozenset(("text", "plain text", "paragraph_hybrid"))
_FURNITURE = frozenset(("header", "footer", "page number", "page_number"))
_PROTECTED = re.compile(
    r"<code\b[^>]*>.*?</code>|https?://\S+|<[^>]*>|\{[^{}]*\}|\[[^\[\]]*\]|%[sd]",
    re.IGNORECASE | re.DOTALL,
)
_STYLE = re.compile(r"</?style\b[^>]*>", re.IGNORECASE)
_WORD = re.compile(r"(?<![A-Za-z-])[A-Za-z]{3,}(?![A-Za-z-])")
_WRAPPED_WORD = re.compile(r"\b([A-Za-z]{2,})-[ \t]*\n\s*([a-z]{2,})\b")
_SOFT_HYPHEN = re.compile(r"(?<=[A-Za-z])\u00ad\s*(?=[A-Za-z])")


def _prose_runs(text):
    end = 0
    for token in _PROTECTED.finditer(text):
        yield end, text[end:token.start()]
        end = token.end()
    yield end, text[end:]


def normalize_body_words(text, words):
    """Join actual line wraps with word evidence; soft hyphens are discretionary."""
    edits = []
    for offset, run in _prose_runs(text):
        for match in _SOFT_HYPHEN.finditer(run):
            edits.append((offset + match.start(), offset + match.end(), ""))
        for match in _WRAPPED_WORD.finditer(run):
            joined = match[1] + match[2]
            if joined.casefold() in words:
                edits.append((offset + match.start(), offset + match.end(), joined))
    for start, end, replacement in sorted(edits, reverse=True):
        text = text[:start] + replacement + text[end:]
    return text


def _edge_run(text, first):
    runs = list(_prose_runs(text))
    for offset, run in runs if first else reversed(runs):
        if not run.strip():
            continue
        outside = text[:offset] if first else text[offset + len(run):]
        # Style wrappers may surround prose; formulas, citations and code are barriers.
        if _STYLE.sub("", outside).strip():
            return None
        return offset, run
    return None


def _context(text, tail=False):
    text = _STYLE.sub("", text)
    text = _PROTECTED.sub(" [protected content] ", text)
    text = " ".join(text.split())
    return text[-400:] if tail else text[:400]


def _is_boundary(left_page, left, right_page, right):
    if not left.box or not right.box:
        return False
    if left_page is not right_page:
        a, b = left_page.page_number, right_page.page_number
        return a is not None and b == a + 1
    boxes = left.box, right.box
    if any(value is None for box in boxes for value in (box.x, box.x2, box.y2)):
        return False
    return right.box.x >= left.box.x2 - 5 and right.box.y2 > left.box.y2 + 20


@dataclass(frozen=True)
class BodyInput:
    source: str
    text: str
    previous_context: str = ""
    next_context: str = ""
    continuation_risk: bool = False
    order_key: int = 0
    continuation_group: int | None = None

    @property
    def context(self):
        return {key: value for key, value in (
            ("previous_fragment", self.previous_context),
            ("next_fragment", self.next_context),
        ) if value}


class BodyInputPlan:
    def __init__(self, docs, prepared_texts, *, evidence=()):
        # All strings are captured before workers mutate any paragraph translations.
        words = frozenset(word.casefold() for text in chain(prepared_texts.values(), evidence)
                          for _, run in _prose_runs(text) for word in _WORD.findall(run))
        texts = {key: normalize_body_words(value, words)
                 for key, value in prepared_texts.items()}
        contexts = {key: {} for key in texts}
        risks = set()
        order = {id(p): index for index, p in enumerate(p for page in docs.page for p in page.pdf_paragraph)}
        groups = {}
        previous = None
        for page in docs.page:
            for paragraph in page.pdf_paragraph:
                key = id(paragraph)
                if key not in texts:
                    if paragraph.layout_label not in _FURNITURE:
                        previous = None
                    continue
                if previous is not None:
                    left_page, left = previous
                    left_key = id(left)
                    if _is_boundary(left_page, left, page, paragraph):
                        left_edge = _edge_run(texts[left_key], False)
                        right_edge = _edge_run(texts[key], True)
                        if left_edge and right_edge:
                            lo, lr = left_edge
                            ro, rr = right_edge
                            article = re.search(r"\b([Aa]n?|[Tt]he)\s*$", lr)
                            prefix = re.search(r"\b([A-Za-z]{2,})([-\u00ad])\s*$", lr)
                            suffix = re.match(r"\s*([a-z]{2,})\b([.,;:!?]?)", rr)
                            continues = bool(article or prefix or (
                                lr.rstrip() and lr.rstrip()[-1] not in ".!?;:"
                                and re.match(r"\s*[a-z]", rr)
                            ))
                            if continues:
                                risks.update((left_key, key))
                                groups[left_key] = groups.get(left_key, order[left_key])
                                groups[key] = groups[left_key]
                                contexts[left_key]["next_context"] = _context(texts[key])
                                contexts[key]["previous_context"] = _context(texts[left_key], True)
                            # A lone article must follow prose, and the next fragment starts a word.
                            if (article and lr[:article.start()].strip()
                                    and (article[1].islower() or lr[:article.start()].rstrip()[-1] in ".!?")
                                    and re.match(r"\s*[a-z]", rr)):
                                start = lo + article.start()
                                texts[left_key] = texts[left_key][:start] + texts[left_key][lo + article.end():]
                                pos = ro + len(rr) - len(rr.lstrip())
                                texts[key] = texts[key][:pos] + article[1] + " " + texts[key][pos:]
                            elif prefix and suffix and (prefix[2] == "\u00ad" or (prefix[1] + suffix[1]).casefold() in words):
                                remainder = texts[key][:ro + suffix.start()] + texts[key][ro + suffix.end():]
                                if not any(re.search(r"[A-Za-z]{2,}", run) for _, run in _prose_runs(remainder)):
                                    previous = page, paragraph
                                    continue
                                start, end = lo + prefix.start(), lo + prefix.end()
                                texts[left_key] = texts[left_key][:start] + prefix[1] + suffix[1] + suffix[2] + texts[left_key][end:]
                                texts[key] = remainder
                previous = page, paragraph
        self.entries = MappingProxyType({key: BodyInput(
            source=prepared_texts[key], text=text, continuation_risk=key in risks,
            order_key=order[key], continuation_group=groups.get(key),
            **contexts[key],
        ) for key, text in texts.items()})
        self.words = words

    def get(self, paragraph):
        return self.entries.get(id(paragraph))

    def apply(self, paragraph, translate_input):
        entry = self.get(paragraph)
        if entry is None:
            return
        if translate_input.unicode != entry.source:
            raise ValueError("body_input_snapshot_mismatch")
        translate_input.unicode = entry.text
        translate_input.body_context = entry.context
        translate_input.continuation_risk = entry.continuation_risk
        translate_input.order_key = entry.order_key
        translate_input.continuation_group = entry.continuation_group
