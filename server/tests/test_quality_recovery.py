import tempfile
import sqlite3
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from babeldoc.format.pdf.document_il import Document, Page, PdfParagraph, PdfParagraphComposition, PdfSameStyleUnicodeCharacters
from babeldoc.format.pdf.translation_recovery import TranslationRecovery


class QualityRecoveryTests(unittest.TestCase):
    def make(self, directory, callback=None):
        recovery = TranslationRecovery(Path(directory) / "paragraph-recovery.sqlite3", "same-input", callback)
        self.addCleanup(recovery.close)
        paragraphs = [PdfParagraph(debug_id=f"p{i}", unicode=f"This is body paragraph {i}.",
                                  pdf_paragraph_composition=[PdfParagraphComposition(
                                      pdf_same_style_unicode_characters=PdfSameStyleUnicodeCharacters(
                                          unicode=f"This is body paragraph {i}."))]) for i in range(3)]
        recovery.register(Document(page=[Page(page_number=0, pdf_paragraph=paragraphs)]),
                          SimpleNamespace(skip_references=False, min_text_length=3))
        return recovery, paragraphs

    def test_concurrent_actual_attempt_reservations_never_exceed_eight(self):
        with tempfile.TemporaryDirectory() as directory:
            recovery, _ = self.make(directory)
            recovery.configure_review(1)
            with ThreadPoolExecutor(max_workers=12) as pool:
                reserved = list(pool.map(lambda _: recovery.reserve_review_request(1), range(24)))
            self.assertEqual(sum(reserved), 8)
            self.assertEqual(recovery.quality_snapshot()["requestsUsed"], 8)
            recovery.close()

    def test_budget_survives_restart_and_explicit_new_attempt_has_own_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            first, _ = self.make(directory)
            self.assertTrue(first.reserve_review_request(1))
            first.close()
            resumed, _ = self.make(directory)
            resumed.configure_review(1)
            self.assertEqual(resumed.quality_snapshot()["requestsUsed"], 1)
            self.assertEqual(sum(resumed.reserve_review_request(1) for _ in range(10)), 7)
            resumed.configure_review(2)
            self.assertEqual(resumed.quality_snapshot()["requestsUsed"], 0)
            self.assertTrue(resumed.reserve_review_request(2))
            resumed.close()

    def test_confirmed_failure_is_atomically_not_restorable_and_unknown_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            recovery, paragraphs = self.make(directory)
            recovery.configure_review(1)
            for paragraph in paragraphs:
                recovery.record(paragraph, "succeeded", input=paragraph.unicode, translation="这是原有译文。")
            recovery.record_quality(paragraphs[0], "failed", "review-v1", "invalid_review")
            recovery.record_quality(paragraphs[1], "unchecked", "review-v1", "provider_error")
            recovery.record_quality(paragraphs[2], "corrected", "review-v1")
            self.assertIsNone(recovery.restored(paragraphs[0], paragraphs[0].unicode))
            self.assertEqual(recovery.restored(paragraphs[1], paragraphs[1].unicode), "这是原有译文。")
            summary, failures = recovery.snapshot()
            self.assertEqual(summary["failed"], 1)
            self.assertEqual(failures[0]["reason"], "semantic_error_unresolved")
            self.assertEqual(recovery.quality_snapshot(), {
                "selected": 3, "checked": 2, "passed": 0, "corrected": 1, "unchecked": 1,
                "failed": 1, "notSelected": 0, "requestsUsed": 0, "requestLimit": 8, "paragraphLimit": 20,
            })
            recovery.close()
            reopened, new_paragraphs = self.make(directory)
            reopened.configure_review(1)
            self.assertIsNone(reopened.restored(new_paragraphs[0], new_paragraphs[0].unicode))
            self.assertEqual(reopened.quality_snapshot()["failed"], 1)
            reopened.close()

    def test_quality_notifications_release_persistence_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            events = []
            recovery, paragraphs = self.make(directory)

            def notify(event):
                result = []
                worker = threading.Thread(target=lambda: result.append(recovery.quality_snapshot()))
                worker.start()
                worker.join(timeout=1)
                self.assertFalse(worker.is_alive())
                events.append(event)

            recovery.callback = notify
            recovery.configure_review(1)
            recovery.record_quality(paragraphs[0], "pending", "review-v1")
            recovery.finish()
            self.assertEqual(events[-1]["qualitySummary"]["unchecked"], 1)
            recovery.close()

    def test_failed_budget_transaction_does_not_advance_or_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            events = []
            recovery, _ = self.make(directory, events.append)
            recovery.configure_review(1)
            before = len(events)
            recovery.connection.set_authorizer(lambda action, table, *args:
                sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_INSERT and table == "metadata" else sqlite3.SQLITE_OK)
            with self.assertRaises(sqlite3.DatabaseError):
                recovery.reserve_review_request(1)
            recovery.connection.set_authorizer(None)
            self.assertEqual(recovery.quality_snapshot()["requestsUsed"], 0)
            self.assertEqual(len(events), before)
            self.assertTrue(recovery.reserve_review_request(1))
            recovery.close()

    def test_approval_is_reusable_after_restart_but_never_for_a_new_translation(self):
        with tempfile.TemporaryDirectory() as directory:
            recovery, paragraphs = self.make(directory)
            paragraph = paragraphs[0]
            source, output = paragraph.unicode, "这是通过复核的译文。"
            recovery.record(paragraph, "succeeded", input=source, translation=output)
            recovery.record_quality(paragraph, "corrected", "review-v1", priority=2)
            recovery.close()
            restored, paragraphs = self.make(directory)
            paragraph = paragraphs[0]
            self.assertEqual(restored.reviewed_output(paragraph, source, output, "review-v1"),
                             {"output": output, "version": "review-v1", "priority": 2})
            self.assertIsNone(restored.reviewed_output(paragraph, source, output, "review-v2"))
            self.assertIsNone(restored.reviewed_output(paragraph, source + "changed", output, "review-v1"))
            # Ordinary result persistence keeps diagnostics, but cannot transfer the approval.
            restored.record(paragraph, "succeeded", input=source, translation="这是另一份未经复核的译文。")
            self.assertIsNone(restored.reviewed_output(paragraph, source, "这是另一份未经复核的译文。", "review-v1"))
            restored.close()


if __name__ == "__main__":
    unittest.main()
