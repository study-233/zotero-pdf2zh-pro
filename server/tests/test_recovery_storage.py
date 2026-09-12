import hashlib
import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from babeldoc.format.pdf.document_il import (
    Document, Page, PdfParagraph, PdfParagraphComposition, PdfCharacter,
)
from babeldoc.format.pdf.translation_recovery import TranslationRecovery


SOURCE = "This is a complete English paragraph about the experiment."
TARGET = "这是关于实验的完整中文段落。"


class RecoveryStorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = self.directory / "paragraph-recovery.sqlite3"
        self.config = SimpleNamespace(skip_references=False, min_text_length=3)

    def recovery(self, fingerprint="document-config", callback=None):
        recovery = TranslationRecovery(self.path, fingerprint, callback)
        self.addCleanup(recovery.close)
        return recovery

    def document(self, count=2):
        paragraphs = [PdfParagraph(debug_id=str(index), unicode=f"{SOURCE} {index}",
                                   pdf_paragraph_composition=[PdfParagraphComposition(
                                       pdf_character=PdfCharacter(char_unicode="T"))])
                      for index in range(count)]
        return Document(page=[Page(page_number=0, pdf_paragraph=paragraphs)]), paragraphs

    def legacy_file(self):
        docs, paragraphs = self.document()
        entries = {}
        for index, paragraph in enumerate(paragraphs):
            key = hashlib.sha256(f"0:{index}:{paragraph.unicode}".encode()).hexdigest()
            entries[key] = dict(page=1, paragraphId=paragraph.debug_id,
                                source=paragraph.unicode, input=paragraph.unicode,
                                status="succeeded", translation=TARGET,
                                attempts=1, context="shared migration prompt", reason=None)
        legacy = self.path.with_suffix(".json")
        legacy.write_text(json.dumps(dict(version=1, fingerprint="document-config",
                                         paragraphs=entries)), encoding="utf-8")
        return legacy, docs, paragraphs

    def test_legacy_migrates_once_and_preserves_original_file(self):
        legacy, docs, paragraphs = self.legacy_file()
        original = legacy.read_bytes()
        recovery = self.recovery()
        self.assertEqual(recovery.connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(recovery.connection.execute("SELECT count(*) FROM contexts").fetchone()[0], 1)
        self.assertEqual(recovery.connection.execute("SELECT count(*) FROM paragraphs").fetchone()[0], 2)
        recovery.register(docs, self.config)
        for paragraph in paragraphs:
            self.assertEqual(recovery.restored(paragraph, paragraph.unicode), TARGET)
        self.assertEqual(legacy.read_bytes(), original)
        recovery.close()
        legacy.write_text("broken after migration", encoding="utf-8")
        restored = self.recovery()
        restored.register(docs, self.config)
        self.assertEqual(restored.restored(paragraphs[0], paragraphs[0].unicode), TARGET)

    def test_configuration_change_clears_database_without_reimporting_legacy(self):
        legacy, _, _ = self.legacy_file()
        original = legacy.read_bytes()
        self.recovery().close()
        changed = self.recovery("different-config")
        self.assertEqual(changed.entries, {})
        self.assertEqual(changed.connection.execute("SELECT count(*) FROM contexts").fetchone()[0], 0)
        changed.close()
        self.assertEqual(self.recovery().entries, {})
        self.assertEqual(legacy.read_bytes(), original)

    def test_interrupted_migration_rolls_back_and_can_be_retried(self):
        legacy, _, _ = self.legacy_file()
        original_bytes = legacy.read_bytes()
        original_upsert = TranslationRecovery._upsert
        attempts = []

        def interrupt(recovery, key, entry):
            original_upsert(recovery, key, entry)
            attempts.append(key)
            if len(attempts) == 2:
                raise RuntimeError("simulated migration interruption")

        with patch.object(TranslationRecovery, "_upsert", interrupt):
            with self.assertRaisesRegex(RuntimeError, "migration interruption"):
                self.recovery()
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall(), [])
        recovery = self.recovery()
        self.assertEqual(len(recovery.entries), 2)
        self.assertEqual(legacy.read_bytes(), original_bytes)

    def test_invalid_or_mismatched_legacy_starts_empty(self):
        legacy = self.path.with_suffix(".json")
        legacy.write_text("not JSON", encoding="utf-8")
        self.assertEqual(self.recovery().entries, {})
        legacy, _, _ = self.legacy_file()
        # Initialization was already committed, so a later legacy file is ignored.
        self.assertEqual(self.recovery().entries, {})
        self.path = self.directory / "mismatched.sqlite3"
        legacy, _, _ = self.legacy_file()
        original = legacy.read_bytes()
        self.assertEqual(self.recovery("different-config").entries, {})
        self.assertEqual(legacy.read_bytes(), original)

    def test_committed_success_survives_close_without_finishing_pending_rows(self):
        recovery = self.recovery()
        docs, paragraphs = self.document()
        recovery.register(docs, self.config)
        recovery.attempt(paragraphs, "one request context")
        recovery.record(paragraphs[0], "succeeded", input=paragraphs[0].unicode,
                        translation=TARGET, reason=None)
        recovery.close()
        restarted = self.recovery()
        self.assertEqual({entry["status"] for entry in restarted.entries.values()}, {"pending", "succeeded"})
        restarted.register(docs, self.config)
        self.assertEqual(restarted.restored(paragraphs[0], paragraphs[0].unicode), TARGET)
        self.assertIsNone(restarted.restored(paragraphs[1], paragraphs[1].unicode))
        # Registering pending work must not destroy the saved success checkpoint.
        restarted.close()
        resumed = self.recovery()
        resumed.register(docs, self.config)
        self.assertEqual(resumed.restored(paragraphs[0], paragraphs[0].unicode), TARGET)
        resumed.record(paragraphs[0], "succeeded", input=paragraphs[0].unicode, translation=TARGET)
        resumed.attempt([paragraphs[1]], "retry context")
        resumed.record(paragraphs[1], "succeeded", input=paragraphs[1].unicode, translation=TARGET)
        resumed.finish()
        self.assertEqual(resumed.snapshot()[0], dict(total=2, succeeded=2, skipped=0, failed=0, pending=0))
        resumed.close()
        repeated = self.recovery()
        repeated.register(docs, self.config)
        for paragraph in paragraphs:
            self.assertEqual(repeated.restored(paragraph, paragraph.unicode), TARGET)

    def test_attempt_is_atomic_and_failed_transaction_does_not_notify(self):
        events = []
        recovery = self.recovery(callback=events.append)
        docs, paragraphs = self.document(3)
        recovery.register(docs, self.config)
        original_upsert = recovery._upsert
        blocked_key = recovery.keys[id(paragraphs[1])]

        def fail_second(key, entry):
            if key == blocked_key:
                raise sqlite3.OperationalError("simulated write failure")
            original_upsert(key, entry)

        with patch.object(recovery, "_upsert", side_effect=fail_second):
            with self.assertRaises(sqlite3.OperationalError):
                recovery.attempt(paragraphs, "batch context")
        self.assertEqual(len(events), 1)
        self.assertEqual(recovery.connection.execute("SELECT count(*) FROM contexts").fetchone()[0], 0)
        self.assertTrue(all(entry["attempts"] == 0 for entry in recovery.current.values()))
        stored = [json.loads(row[0]) for row in recovery.connection.execute("SELECT payload FROM paragraphs")]
        self.assertTrue(all(entry["attempts"] == 0 for entry in stored))
        recovery.attempt(paragraphs, "batch context")
        recovery.attempt(paragraphs, "batch context")
        self.assertTrue(all(entry["attempts"] == 2 for entry in recovery.current.values()))
        self.assertEqual(recovery.connection.execute("SELECT count(*) FROM contexts").fetchone()[0], 1)
        self.assertNotIn("batch context", "".join(row[0] for row in recovery.connection.execute("SELECT payload FROM paragraphs")))

    def test_parallel_updates_and_repeated_registration_keep_counts_correct(self):
        recovery = self.recovery()
        docs, paragraphs = self.document(32)
        recovery.register(docs, self.config)

        def translate(paragraph):
            recovery.attempt([paragraph], "same context")
            recovery.record(paragraph, "succeeded", input=paragraph.unicode, translation=TARGET)

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(translate, paragraphs))
        self.assertEqual(recovery.snapshot()[0], dict(total=32, succeeded=32, skipped=0, failed=0, pending=0))
        self.assertEqual(recovery.connection.execute("SELECT count(*) FROM contexts").fetchone()[0], 1)
        recovery.fail(paragraphs[0], RuntimeError("untrusted details"))
        self.assertEqual(len(recovery.snapshot()[1]), 1)
        recovery.record(paragraphs[0], "succeeded", reason=None)
        self.assertEqual(recovery.snapshot()[1], [])
        recovery.register(docs, self.config)
        self.assertEqual(recovery.snapshot()[0], dict(total=32, succeeded=0, skipped=0, failed=0, pending=32))
        for paragraph in paragraphs:
            self.assertEqual(recovery.restored(paragraph, paragraph.unicode), TARGET)

    def test_notifications_are_throttled_committed_and_outside_storage_lock(self):
        recovery = self.recovery()
        docs, paragraphs = self.document(3)
        events = []
        clock = [100.0]
        with ThreadPoolExecutor(max_workers=1) as reader:
            def callback(event):
                # A different thread can read the current snapshot during the callback.
                snapshot = reader.submit(recovery.snapshot).result(timeout=1)
                self.assertEqual(snapshot[0], event["translationSummary"])
                with closing(sqlite3.connect(self.path)) as connection:
                    self.assertEqual(connection.execute("SELECT count(*) FROM paragraphs").fetchone()[0], 3)
                events.append(event)

            recovery.callback = callback
            with patch("babeldoc.format.pdf.translation_recovery.time.monotonic", side_effect=lambda: clock[0]):
                recovery.register(docs, self.config)
                recovery.record(paragraphs[0], "succeeded")
                recovery.fail(paragraphs[1], RuntimeError("not persisted"))
                self.assertEqual(len(events), 1)
                clock[0] = 101.0
                recovery.attempt([paragraphs[2]], "context")
                self.assertEqual(len(events), 2)
                recovery.record(paragraphs[1], "succeeded")
                recovery.finish()
                self.assertEqual(len(events), 3)
                self.assertEqual(events[-1]["translationSummary"]["pending"], 0)
                self.assertEqual(events[-1]["translationSummary"]["failed"], 1)
                recovery.close()
                self.assertEqual(len(events), 3)
        recovery.callback = None

    def test_close_flushes_pending_summary_without_failing_cancelled_work(self):
        events = []
        recovery = self.recovery(callback=events.append)
        docs, paragraphs = self.document()
        with patch("babeldoc.format.pdf.translation_recovery.time.monotonic", return_value=100.0):
            recovery.register(docs, self.config)
            recovery.attempt(paragraphs, "in-flight request")
            self.assertEqual(len(events), 1)
            recovery.close()
            self.assertEqual(len(events), 2)
        self.assertEqual(events[-1]["translationSummary"], dict(total=2, succeeded=0, skipped=0, failed=0, pending=2))
        with self.assertRaises(sqlite3.ProgrammingError):
            recovery.connection.execute("SELECT 1")
        restarted = self.recovery()
        self.assertTrue(all(entry["status"] == "pending" for entry in restarted.entries.values()))


if __name__ == "__main__":
    unittest.main()
