"""Real extraction plus HTTP ownership, cancellation and privacy regressions."""
import asyncio
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from flask import Flask
import pymupdf

from diagnostics import DiagnosticStore
from document_export import install_document_export_routes
from pdf2zh_pro.document import export_document
from server import save_pdf_input


def sample_pdf(text="Export sample paper", chinese=False):
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((72, 100), text, fontname="china-s" if chinese else "helv", fontsize=18)
        page.insert_text((72, 140), "A paragraph with table and formula context.")
        return document.tobytes()


class DocumentExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = DiagnosticStore()
        self.app = Flask(__name__)
        install_document_export_routes(self.app, lambda: self.root, lambda: self.store, save_pdf_input)
        self.client = self.app.test_client()
        self.headers = {"X-PDF2ZH-Client": "zotero", "X-PDF2ZH-Request-ID": "test-export-request-123"}

    def upload(self, client=None, format="txt", **kwargs):
        data = {"file": (io.BytesIO(sample_pdf()), "paper.pdf")}
        if format is not None:
            data["format"] = format
        return (client or self.client).post("/document-exports", headers=self.headers,
            data=data, **kwargs)

    def test_selected_formats_are_direct_utf8_files_with_matching_headers(self):
        for format, mime in (("md", "text/markdown"), ("json", "application/json"), ("txt", "text/plain")):
            with self.subTest(format=format):
                self.headers["X-PDF2ZH-Request-ID"] = "test-export-format-" + format
                response = self.upload(format=format, buffered=True)
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(response.mimetype, mime)
                self.assertIn(f'filename="document-export.{format}"', response.headers["Content-Disposition"])
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertFalse(response.data.startswith(b"PK"))
                self.assertIn("Export sample paper", response.data.decode("utf-8"))
                if format == "json":
                    structured = json.loads(response.data)
                    self.assertEqual(structured["filename"], "paper.pdf")
                    self.assertEqual(structured["page_count"], 1)
                    self.assertIn("boxes", structured["pages"][0])
                    self.assertNotIn(str(self.root), response.data.decode("utf-8"))
                response.close()
                self.assert_clean()
                self.assertFalse((self.root / "tasks.json").exists())

    def test_invalid_format_never_starts_worker_or_writes_output(self):
        for index, format in enumerate((None, "", "zip", "../../bad")):
            with self.subTest(format=format), patch("document_export.run_translation") as worker:
                self.headers["X-PDF2ZH-Request-ID"] = f"test-export-invalid-{index}"
                response = self.upload(format=format)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json["code"], "export_invalid_request")
                worker.assert_not_called()
        self.assert_clean()

    def assert_clean(self):
        self.assertEqual(list(self.root.glob("export-*")), [])
        self.assertEqual(list(self.root.rglob("lease-*.json")), [])

    def test_translated_pdf_retains_unicode_and_original_bytes(self):
        source = self.root / "译文.pdf"
        original = sample_pdf("机器学习论文译文", chinese=True)
        source.write_bytes(original)
        for format in ("md", "json", "txt"):
            with self.subTest(format=format):
                output = self.root / f"result.{format}"
                self.assertEqual(export_document(source, output, format=format), {"pages": 1})
                self.assertIn("机器学习论文译文", output.read_text(encoding="utf-8"))
                self.assertEqual(source.read_bytes(), original)

    def test_plain_text_export_does_not_generate_other_formats(self):
        source = self.root / "paper.pdf"
        source.write_bytes(sample_pdf())
        with patch.object(pymupdf.Document, "to_markdown", side_effect=AssertionError("Unselected Markdown")), \
             patch.object(pymupdf.Document, "to_json", side_effect=AssertionError("Unselected JSON")):
            self.assertEqual(export_document(source, self.root / "paper.txt", format="txt"), {"pages": 1})

    def test_image_only_pdf_reports_ocr_requirement_and_encryption_is_rejected(self):
        source = self.root / "empty.pdf"
        with pymupdf.open() as document:
            document.new_page()
            document.save(source)
            encrypted = self.root / "encrypted.pdf"
            document.save(encrypted, encryption=pymupdf.PDF_ENCRYPT_AES_256,
                          owner_pw="owner", user_pw="reader")
        for format in ("md", "json", "txt"):
            with self.subTest(format=format):
                output = self.root / f"result.{format}"
                self.assertEqual(export_document(source, output, format=format)["code"], "export_needs_ocr")
                self.assertEqual(export_document(encrypted, output, format=format)["code"], "export_invalid_pdf")
                self.assertFalse(output.exists())

    def test_pre_cancelled_request_and_browser_requests_never_start_worker(self):
        with patch("document_export.run_translation") as worker:
            self.assertEqual(self.client.post("/document-exports").status_code, 403)
            self.assertEqual(self.client.post("/document-exports", headers={**self.headers, "Origin":"https://untrusted.invalid"}).status_code, 403)
            self.assertEqual(self.client.post("/document-exports/test-export-request-123/cancel", headers=self.headers).status_code, 200)
            self.assertEqual(self.upload().json["code"], "export_cancelled")
            worker.assert_not_called()
        self.assert_clean()

    def test_cancel_and_busy_share_bounded_supervisor_without_history_writes(self):
        started = threading.Event()
        response = []

        def worker(*args, cancel_event, **kwargs):
            started.set()
            self.assertTrue(cancel_event.wait(5))
            raise asyncio.CancelledError

        with patch("document_export.run_translation", side_effect=worker):
            thread = threading.Thread(target=lambda: response.append(self.upload(self.app.test_client())))
            thread.start()
            try:
                self.assertTrue(started.wait(5))
                self.assertEqual(self.upload().status_code, 429)
                self.client.post("/document-exports/test-export-request-123/cancel", headers=self.headers)
            finally:
                thread.join(6)
            self.assertFalse(thread.is_alive())
        self.assertEqual(response[0].json["code"], "export_cancelled")
        self.assert_clean()

    def test_timeout_and_failure_are_fixed_codes_and_diagnostics_have_no_content(self):
        def worker(*args, cancel_event, **kwargs):
            self.assertTrue(cancel_event.wait(5))
            raise asyncio.CancelledError
        with patch("document_export.EXPORT_TIMEOUT_SECONDS", .05), patch("document_export.run_translation", side_effect=worker):
            self.assertEqual(self.upload().json["code"], "export_timeout")
        self.headers["X-PDF2ZH-Request-ID"] += "-retry"
        with patch("document_export.run_translation", side_effect=RuntimeError("SECRET PDF CONTENT /private/source.pdf")):
            response = self.upload()
        self.assertEqual(response.json, {"status":"error", "code":"export_failed"})
        evidence = json.dumps(self.store.export())
        self.assertNotIn("SECRET", evidence)
        self.assertNotIn("/private/source.pdf", evidence)
        self.assertIn("document_export_failed", evidence)
        self.assert_clean()

    def test_invalid_upload_does_not_leave_workspace(self):
        response = self.client.post("/document-exports", headers=self.headers,
            data={"file": (io.BytesIO(b""), "paper.pdf"), "format": "txt"})
        self.assertEqual(response.status_code, 400)
        self.assert_clean()

    def test_corrupt_pdf_is_a_safe_validation_error(self):
        source = self.root / "broken.pdf"
        source.write_bytes(b"%PDF- broken PRIVATE CONTENT")
        self.assertEqual(export_document(source, self.root / "result.txt", format="txt"), {"code": "export_invalid_pdf"})


if __name__ == "__main__":
    unittest.main()
