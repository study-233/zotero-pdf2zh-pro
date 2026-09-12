"""Durable paragraph outcomes. A rendered PDF alone does not imply success."""
import hashlib
import json
import sqlite3
import threading
import time
from collections import Counter
from pathlib import Path

from babeldoc.format.pdf.document_il.midend.reference_filter import find_reference_paragraph_ids
from babeldoc.format.pdf.document_il.utils.paragraph_helper import (
    is_cid_paragraph, is_placeholder_only_paragraph, is_pure_numeric_paragraph,
    is_url_only_paragraph,
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
        "semantic_error_unresolved", "invalid_review",
    }:
        chain.insert(0, str(error))
    return {"errorType": type(error).__name__, "statusCode": status if isinstance(status, int) else None,
            "reason": " → ".join(chain), "providerCode": code}


class TranslationRecovery:
    VERSION = 1
    QUALITY_STATES = frozenset(("not_selected", "pending", "passed", "corrected", "unchecked", "failed"))
    QUALITY_REASONS = frozenset(("budget_exhausted", "provider_error", "invalid_review", "unsupported", "semantic_error_unresolved"))

    def __init__(self, path, fingerprint, callback=None):
        requested_path = Path(path)
        self.path = requested_path.with_suffix(".sqlite3")
        self.fingerprint = fingerprint
        self.callback = callback
        self.lock = threading.RLock()
        self._notification_lock = threading.Lock()
        self._last_notification = float("-inf")
        self._notification_pending = False
        self.current = {}
        self.keys = {}
        self._summary = dict(total=0, succeeded=0, skipped=0, failed=0, pending=0)
        self._failed = {}
        self._pending = set()
        self._quality_counts = Counter()
        self._review_enabled = False
        self._review_attempt = 1
        self._review_requests_used = 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        try:
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA synchronous=FULL")
            self.connection.execute("PRAGMA foreign_keys=ON")
            self._initialize(requested_path.with_suffix(".json"))
            self.entries = {}
            for key, payload, context_id in self.connection.execute(
                "SELECT key, payload, context_id FROM paragraphs"
            ):
                entry = json.loads(payload)
                if context_id is not None:
                    entry["contextId"] = context_id
                self.entries[key] = entry
        except BaseException:
            self.connection.close()
            raise

    def _initialize(self, legacy_path):
        # Schema, migration and its marker commit together. A restart before
        # commit retries initialization; an initialized database never reimports.
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            self.connection.execute("CREATE TABLE IF NOT EXISTS contexts (id INTEGER PRIMARY KEY, prompt TEXT NOT NULL UNIQUE)")
            self.connection.execute("CREATE TABLE IF NOT EXISTS paragraphs (key TEXT PRIMARY KEY, payload TEXT NOT NULL, context_id INTEGER REFERENCES contexts(id))")
            metadata = dict(self.connection.execute("SELECT key, value FROM metadata"))
            if not metadata:
                for key, entry in self._legacy_entries(legacy_path).items():
                    entry = dict(entry)
                    context = entry.pop("context", None)
                    if isinstance(context, str):
                        entry["contextId"] = self._context_id(context)
                    self._upsert(key, entry)
                self.connection.executemany(
                    "INSERT INTO metadata (key, value) VALUES (?, ?)",
                    [("version", str(self.VERSION)), ("fingerprint", self.fingerprint)],
                )
            elif metadata.get("version") != str(self.VERSION):
                raise ValueError("Unsupported recovery database version")
            elif metadata.get("fingerprint") != self.fingerprint:
                self.connection.execute("DELETE FROM paragraphs")
                self.connection.execute("DELETE FROM contexts")
                self.connection.execute("UPDATE metadata SET value=? WHERE key='fingerprint'", (self.fingerprint,))

    def _legacy_entries(self, path):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if (not isinstance(data, dict) or data.get("version") != self.VERSION
                or data.get("fingerprint") != self.fingerprint):
            return {}
        entries = data.get("paragraphs", {})
        if not isinstance(entries, dict):
            return {}
        return {key: entry for key, entry in entries.items()
                if isinstance(entry, dict) and entry.get("status") in self._summary
                and entry.get("status") != "total"}

    def _context_id(self, context):
        self.connection.execute("INSERT OR IGNORE INTO contexts (prompt) VALUES (?)", (context,))
        return self.connection.execute("SELECT id FROM contexts WHERE prompt=?", (context,)).fetchone()[0]

    def _upsert(self, key, entry):
        payload = {name: value for name, value in entry.items() if name != "contextId"}
        self.connection.execute(
            "INSERT INTO paragraphs (key, payload, context_id) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET payload=excluded.payload, context_id=excluded.context_id",
            (key, json.dumps(payload, ensure_ascii=False), entry.get("contextId")),
        )

    def _commit(self, changes, context=None):
        persisted = {}
        with self.connection:
            context_id = self._context_id(context) if context is not None and changes else None
            for key, entry in changes.items():
                if context_id is not None:
                    entry["contextId"] = context_id
                if entry["status"] == "pending" and self.entries.get(key, {}).get("status") == "succeeded":
                    continue
                self._upsert(key, entry)
                persisted[key] = dict(entry)
        # Memory and notifications only advance after the transaction commits.
        self.entries.update(persisted)
        for key, entry in changes.items():
            previous = self.current.get(key)
            if previous is None:
                self._summary["total"] += 1
            else:
                self._summary[previous["status"]] -= 1
                old_quality = previous.get("quality", {}).get("status")
                if old_quality in self.QUALITY_STATES:
                    self._quality_counts[old_quality] -= 1
            self.current[key] = entry
            self._summary[entry["status"]] += 1
            quality = entry.get("quality", {}).get("status")
            if quality in self.QUALITY_STATES:
                self._quality_counts[quality] += 1
            if entry["status"] == "pending":
                self._pending.add(key)
            else:
                self._pending.discard(key)
            if entry["status"] == "failed":
                self._failed[key] = {name: entry.get(name) for name in (
                    "page", "paragraphId", "attempts", "errorType", "statusCode", "providerCode", "reason"
                )}
            else:
                self._failed.pop(key, None)
        self._notification_pending = self._notification_pending or bool(changes)

    def register(self, docs, config):
        skipped = find_reference_paragraph_ids(docs) if config.skip_references else set()
        with self.lock:
            changes = {}
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
                    elif is_url_only_paragraph(paragraph):
                        reason = "url_only"
                    changes[key] = {"page": page_number + 1,
                        "paragraphId": paragraph.debug_id, "source": source,
                        "status": "skipped" if reason else "pending", "reason": reason,
                        "attempts": 0}
                    old_quality = self.entries.get(key, {}).get("quality")
                    if isinstance(old_quality, dict):
                        changes[key]["quality"] = dict(old_quality)
            self._commit(changes)
        self._notify()

    def is_skipped(self, paragraph):
        with self.lock:
            return self.current.get(self.keys.get(id(paragraph)), {}).get("status") == "skipped"

    def restored(self, paragraph, source):
        with self.lock:
            entry = self.entries.get(self.keys.get(id(paragraph)), {})
            if (entry.get("status") == "succeeded" and entry.get("input") == source
                    and entry.get("quality", {}).get("status") != "failed"):
                return entry.get("translation")
        return None

    def configure_review(self, attempt=1):
        if type(attempt) is not int or attempt < 1:
            raise ValueError("Invalid review attempt")
        with self.lock:
            self._review_enabled = True
            self._review_attempt = attempt
            row = self.connection.execute("SELECT value FROM metadata WHERE key=?", (self._review_budget_key(attempt),)).fetchone()
            self._review_requests_used = int(row[0]) if row else 0
            self._notification_pending = True
        self._notify()

    def _review_budget_key(self, attempt):
        return f"review.{self.fingerprint}.{attempt}.requests"

    def reserve_review_request(self, attempt, limit=8):
        """Durably reserve before a real provider attempt, including retries."""
        if type(attempt) is not int or attempt < 1 or not 0 < limit <= 8:
            raise ValueError("Invalid review budget")
        with self.lock:
            key = self._review_budget_key(attempt)
            with self.connection:
                self.connection.execute("BEGIN IMMEDIATE")
                row = self.connection.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
                used = int(row[0]) if row else 0
                if used >= limit:
                    self._review_enabled = True
                    self._review_attempt = attempt
                    self._review_requests_used = used
                    return False
                self.connection.execute(
                    "INSERT INTO metadata(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, str(used + 1)),
                )
            self._review_enabled = True
            self._review_attempt = attempt
            self._review_requests_used = used + 1
            self._notification_pending = True
        self._notify()
        return True

    @staticmethod
    def _approval_fingerprint(source, output):
        return hashlib.sha256(json.dumps([source, output], ensure_ascii=False).encode()).hexdigest()

    def reviewed_output(self, paragraph, source, output, version):
        """Reuse only an approval bound to this exact input and final translation."""
        with self.lock:
            entry = self.entries.get(self.keys.get(id(paragraph)), {})
            quality = entry.get("quality", {})
            priority = quality.get("priority")
            if (entry.get("status") == "succeeded"
                    and entry.get("input") == source and entry.get("translation") == output
                    and quality.get("status") in {"passed", "corrected"}
                    and quality.get("version") == version
                    and type(priority) is int and 0 <= priority <= 4
                    and quality.get("approval") == self._approval_fingerprint(source, output)):
                return {"output": output, "version": version, "priority": priority}
        return None

    def record_quality(self, paragraph, status, version, reason=None, *, priority=0):
        if status not in self.QUALITY_STATES:
            raise ValueError("Unknown quality state")
        if type(priority) is not int or not 0 <= priority <= 4:
            raise ValueError("Invalid quality priority")
        if reason is not None and reason not in self.QUALITY_REASONS:
            reason = "invalid_review"
        with self.lock:
            key = self.keys.get(id(paragraph))
            if key not in self.current:
                return
            self._review_enabled = True
            entry = dict(self.current[key], quality={
                "status": status, "version": version, "attempt": self._review_attempt, "reason": reason,
                "priority": priority,
            })
            if status in {"passed", "corrected"}:
                entry["quality"]["approval"] = self._approval_fingerprint(entry.get("input"), entry.get("translation"))
            if status == "failed":
                # Persist the verdict and non-restorable state in the same transaction.
                entry.update(status="failed", errorType="InvalidTranslation", reason="semantic_error_unresolved")
            self._commit({key: entry})
        self._notify()

    def quality_snapshot(self):
        with self.lock:
            if not self._review_enabled:
                return None
            counts = self._quality_counts
            checked = counts["passed"] + counts["corrected"] + counts["failed"]
            unchecked = counts["pending"] + counts["unchecked"]
            return {
                "selected": checked + unchecked, "checked": checked,
                "passed": counts["passed"], "corrected": counts["corrected"],
                "unchecked": unchecked, "failed": counts["failed"],
                "notSelected": counts["not_selected"], "requestsUsed": self._review_requests_used,
                "requestLimit": 8, "paragraphLimit": 20,
            }

    def record(self, paragraph, status, **values):
        if status not in {"pending", "succeeded", "failed", "skipped"}:
            raise ValueError("Unknown paragraph status")
        with self.lock:
            key = self.keys.get(id(paragraph))
            if key not in self.current:
                return
            entry = dict(self.current[key], status=status, **values)
            self._commit({key: entry})
        self._notify()

    def attempt(self, paragraphs, context):
        with self.lock:
            changes = {}
            for paragraph in paragraphs:
                key = self.keys.get(id(paragraph))
                entry = self.current.get(key)
                if entry is not None:
                    changes[key] = dict(entry, attempts=entry["attempts"] + 1)
            self._commit(changes, context)
        self._notify()

    def fail(self, paragraph, error):
        self.record(paragraph, "failed", **safe_error(error))

    def finish(self):
        with self.lock:
            self._commit({key: dict(self.current[key], status="failed",
                                   errorType="UnprocessedParagraph", reason="paragraph_not_completed")
                          for key in self._pending})
            self._notification_pending = True
        self._notify(force=True)

    def snapshot(self):
        with self.lock:
            return dict(self._summary), [dict(entry) for entry in self._failed.values()]

    def _notify(self, force=False):
        if self.callback is None:
            return
        # Serialize publication without holding the persistence lock during the
        # callback. Another writer may commit while a notification is delivered.
        with self._notification_lock:
            with self.lock:
                now = time.monotonic()
                if not self._notification_pending or (not force and now - self._last_notification < 1):
                    return
                summary, failed = self.snapshot()
                quality = self.quality_snapshot()
                self._last_notification = now
                self._notification_pending = False
            event = {"type": "translation_summary", "translationSummary": summary, "failedParagraphs": failed}
            if quality is not None:
                event["qualitySummary"] = quality
            self.callback(event)

    def close(self):
        try:
            self._notify(force=True)
        finally:
            with self.lock:
                self.connection.close()
