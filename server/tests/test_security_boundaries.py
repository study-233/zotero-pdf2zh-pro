"""Regression coverage for file writes and public error responses."""
import json
import importlib.metadata
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import server
from glossary_manager import GlossaryError
from packaging.version import Version
from test_server import build_pdf_payload


class InstalledDependencyTests(unittest.TestCase):
    def test_active_runtime_meets_security_minimums(self):
        for package, minimum in {"pypdf": "6.21.2", "pillow": "13.0.0",
                                 "filelock": "4.2.1", "certifi": "2026.10.19", "urllib3": "2.8.0",
                                 "fsspec": "2026.6.0", "werkzeug": "3.1.9"}.items():
            with self.subTest(package=package):
                self.assertGreaterEqual(Version(importlib.metadata.version(package)), Version(minimum))


class InputPathTests(unittest.TestCase):
    def payload(self, name):
        return {"fileName": name, "fileContent": build_pdf_payload(), "service": "openai"}

    def test_platform_paths_preserve_unicode_basename(self):
        for name in [r"C:\papers\中文论文.pdf", "/tmp/中文论文.pdf", "../中文论文.pdf", r"..\中文论文.pdf"]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                result = server.prepare_translation_request(self.payload(name), root)
                self.assertEqual(result.file_name, "中文论文.pdf")
                self.assertTrue((root / result.file_name).is_file())
                self.assertEqual(sorted(p.name for p in root.iterdir()), ["output", "中文论文.pdf"])

    def test_invalid_components_never_create_input(self):
        for name in ["paper.pdf:secret", "C:paper.pdf", "NUL.pdf", "COM1.pdf", "LPT².pdf",
                     "..", ".", "paper.pdf.", "bad\x00.pdf", "bad?.pdf"]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(server.RequestValidationError):
                    server.prepare_translation_request(self.payload(name), Path(directory))
                self.assertEqual(list(Path(directory).iterdir()), [])

    def test_existing_file_and_links_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "paper.pdf"
            target.write_bytes(b"original")
            with self.assertRaises(server.RequestValidationError):
                server.prepare_translation_request(self.payload(target.name), root)
            self.assertEqual(target.read_bytes(), b"original")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            outside.mkdir()
            source = outside / "paper.pdf"
            source.write_bytes(b"original")
            workspace = root / "workspace"
            workspace.mkdir()
            try:
                (workspace / source.name).symlink_to(source)
            except OSError:
                self.skipTest("Windows account does not permit symlinks")
            with self.assertRaises(server.RequestValidationError):
                server.prepare_translation_request(self.payload(source.name), workspace)
            self.assertEqual(source.read_bytes(), b"original")


class PublicErrorTests(unittest.TestCase):
    def setUp(self):
        self.client = server.create_app().test_client()
        self.secret = r"Traceback C:\Users\private\token.txt https://user:password@example.test/?api_key=sk-secret"

    def test_unexpected_errors_are_correlated_without_response_or_log_secrets(self):
        cases = [("get", "/glossaries", server.GLOSSARY_MANAGER, "list_packs", None),
                 ("post", "/translate", server, "translate_pdf_request", {}),
                 ("post", "/validate-config", server, "validate_config_request", {}),
                 ("post", "/tasks", server, "prepare_translation_request",
                  {"fileName": "paper.pdf", "fileContent": build_pdf_payload()})]
        for method, url, target, name, data in cases:
            with self.subTest(url=url), patch.object(target, name, side_effect=OSError(self.secret)), \
                    self.assertLogs(server.LOGGER, level="ERROR") as logs:
                response = getattr(self.client, method)(url, json=data)
            self.assertEqual(response.status_code, 500)
            self.assertEqual(response.json["status"], "error")
            self.assertRegex(response.json["errorId"], r"^[0-9a-f]{32}$")
            self.assertIn(response.json["errorId"], "\n".join(logs.output))
            self.assertNotIn(self.secret, json.dumps(response.json))
            for text in [json.dumps(response.json), "\n".join(logs.output)]:
                for forbidden in ["sk-secret", "private", "api_key", "example.test", "password"]:
                    self.assertNotIn(forbidden, text)
            self.assertIn("diagnostics", response.json)
            if url == "/tasks":
                self.assertIn("serverInstanceId", response.json)
                self.assertIn("revision", response.json)

    def test_expected_business_errors_keep_status_and_message(self):
        with patch.object(server.GLOSSARY_MANAGER, "list_packs", side_effect=GlossaryError("Unknown glossary pack", 404)):
            response = self.client.get("/glossaries")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json["message"], "Unknown glossary pack")
        self.assertNotIn("errorId", response.json)

    def test_unhandled_runtime_errors_use_the_same_safe_response(self):
        with patch.object(server.GLOSSARY_MANAGER, "list_packs", side_effect=RuntimeError(self.secret)), \
                self.assertLogs(server.LOGGER, level="ERROR") as logs:
            response = self.client.get("/glossaries")
        self.assertEqual(response.status_code, 500)
        self.assertIn("errorId", response.json)
        self.assertNotIn("sk-secret", json.dumps(response.json) + "\n".join(logs.output))
        self.assertEqual(self.client.get("/missing-route").status_code, 404)

    def test_wrapped_catalog_error_has_a_fixed_public_message(self):
        with patch("glossary_manager.requests.get", side_effect=OSError(self.secret)):
            response = self.client.post("/glossaries/check-updates")
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("sk-secret", json.dumps(response.json))
        self.assertIn("errorId", response.json)


if __name__ == "__main__":
    unittest.main()
