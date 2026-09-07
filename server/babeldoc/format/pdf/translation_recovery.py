"""Durable paragraph outcomes. A rendered PDF alone does not imply success."""
import hashlib
import json
import threading
from pathlib import Path

from babeldoc.format.pdf.document_il.midend.reference_filter import find_reference_paragraph_ids
from babeldoc.format.pdf.document_il.utils.paragraph_helper import (
    is_cid_paragraph, is_placeholder_only_paragraph, is_pure_numeric_paragraph,
)


def safe_error(error):
    """Only fixed labels/types; never provider response text, URLs or credentials."""
    chain, seen = [], set()
    current = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(type(current).__name__)
        current = current.__cause__ or current.__context__
    status = getattr(error, "status_code", None)
    code = getattr(error, "code", None)
    # Provider codes are untrusted. Only retain known machine labels.
    if code not in {"model_not_found", "invalid_api_key", "insufficient_quota", "rate_limit_exceeded", "permission_denied"}:
        code = None
    if type(error).__name__ == "InvalidTranslation" and str(error) in {
        "empty_translation", "invalid_response", "invalid_json", "placeholder_mismatch", "unchanged_translation",
        "target_language_missing", "paragraph_count_mismatch", "invalid_paragraph", "invalid_or_duplicate_paragraph_id",
    }:
        chain.insert(0, str(error))
    return {"errorType": type(error).__name__, "statusCode": status if isinstance(status, int) else None,
            "reason": " → ".join(chain), "providerCode": code}


class TranslationRecovery:
    VERSION = 1

    def __init__(self, path, fingerprint, callback=None):
        self.path = Path(path)
        self.fingerprint = fingerprint
        self.callback = callback
        self.lock = threading.RLock()
        self.entries = {}
        self.current = {}
        self.keys = {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("version") == self.VERSION and data.get("fingerprint") == fingerprint:
                self.entries = data.get("paragraphs", {})
        except (OSError, ValueError):
            pass

    def register(self, docs, config):
        skipped = find_reference_paragraph_ids(docs) if config.skip_references else set()
        with self.lock:
            for page in docs.page:
                for index, paragraph in enumerate(page.pdf_paragraph):
                    if paragraph.debug_id is None or paragraph.unicode is None:
                        continue
                    source = paragraph.unicode
                    page_number = (page.page_number or 0) + getattr(config, "recovery_page_offset", 0)
                    identity = f"{page_number}:{index}:{source}"
                    key = hashlib.sha256(identity.encode()).hexdigest()
                    self.keys[id(paragraph)] = key
                    reason = None
                    if id(paragraph) in skipped:
                        reason = "references"
                    elif paragraph.vertical:
                        reason = "vertical_text"
                    elif is_cid_paragraph(paragraph):
                        reason = "cid_text"
                    elif len(source) < config.min_text_length:
                        reason = "short_text"
                    elif is_pure_numeric_paragraph(paragraph) or is_placeholder_only_paragraph(paragraph):
                        reason = "numeric_or_formula"
                    self.current[key] = {"page": page_number + 1,
                        "paragraphId": paragraph.debug_id, "source": source,
                        "status": "skipped" if reason else "pending", "reason": reason,
                        "attempts": 0}
            self._save()

    def is_skipped(self, paragraph):
        with self.lock:
            return self.current.get(self.keys.get(id(paragraph)), {}).get("status") == "skipped"

    def restored(self, paragraph, source):
        with self.lock:
            entry = self.entries.get(self.keys.get(id(paragraph)), {})
            if entry.get("status") == "succeeded" and entry.get("input") == source:
                return entry.get("translation")
        return None

    def record(self, paragraph, status, **values):
        with self.lock:
            key = self.keys.get(id(paragraph))
            if key not in self.current:
                return
            self.current[key].update(status=status, **values)
            self.entries[key] = dict(self.current[key])
            self._save()

    def attempt(self, paragraphs, context):
        with self.lock:
            for paragraph in paragraphs:
                entry = self.current.get(self.keys.get(id(paragraph)))
                if entry is not None:
                    entry["attempts"] += 1
                    entry["context"] = context
            self._save()

    def fail(self, paragraph, error):
        self.record(paragraph, "failed", **safe_error(error))

    def finish(self):
        with self.lock:
            for key, entry in self.current.items():
                if entry["status"] == "pending":
                    entry.update(status="failed", errorType="UnprocessedParagraph", reason="paragraph_not_completed")
                self.entries[key] = dict(entry)
            self._save()

    def snapshot(self):
        with self.lock:
            summary = {"total": len(self.current), "succeeded": 0, "skipped": 0, "failed": 0, "pending": 0}
            failed = []
            for entry in self.current.values():
                summary[entry["status"]] += 1
                if entry["status"] == "failed":
                    failed.append({k: entry.get(k) for k in ("page", "paragraphId", "attempts", "errorType", "statusCode", "providerCode", "reason")})
            return summary, failed

    def _save(self):
        persisted = dict(self.entries)
        for key, entry in self.current.items():
            if entry["status"] == "pending" and persisted.get(key, {}).get("status") == "succeeded":
                continue
            persisted[key] = entry
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": self.VERSION, "fingerprint": self.fingerprint,
            "paragraphs": persisted}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)
        if self.callback:
            summary, failed = self.snapshot()
            self.callback({"type": "translation_summary", "translationSummary": summary, "failedParagraphs": failed})
