from __future__ import annotations

import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

from pdf2zh_next_service import build_settings_input
from pdf2zh_next_service import collect_output_files
from pdf2zh_next_service import diagnose_service_error
from pdf2zh_next_service import install_text_check_bypass
from pdf2zh_next_service import run_live_translator_test
from pdf2zh_next_service import set_text_checks_skipped
from pdf2zh_next.config.cli_env_model import CLIEnvSettingsModel


def make_settings_payload(**overrides):
    payload = {
        "input_path": "/private/tmp/source.pdf",
        "output_dir": "/private/tmp/output",
        "output_modes": ["dual"],
        "service": "siliconflowfree",
        "source_lang": "en",
        "target_lang": "fr",
        "qps": 99,
        "pool_size": 77,
        "skip_last_pages": 0,
        "ocr": True,
        "auto_ocr": False,
        "translate_table_text": False,
        "skip_references": True,
        "no_watermark": True,
        "no_auto_extract_glossary": True,
        "font_family": "sans-serif",
        "llm_api": {},
    }
    payload.update(overrides)
    return payload


class PDF2zhNextServiceTests(unittest.TestCase):
    def test_collect_output_files_moves_absolute_result_into_workspace(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            generated_path = root / "translated.pdf"
            generated_path.write_bytes(b"pdf")
            output_dir = root / "workspace" / "output"
            output_dir.mkdir(parents=True)

            files = collect_output_files(
                SimpleNamespace(
                    mono_pdf_path=None,
                    dual_pdf_path=generated_path,
                ),
                output_dir,
                ["dual"],
                "paper.pdf",
            )

            persisted_path = output_dir / generated_path.name
            self.assertEqual(files["dual"].output_path, persisted_path)
            self.assertEqual(persisted_path.read_bytes(), b"pdf")
            self.assertFalse(generated_path.exists())

    def test_build_settings_input_disables_auto_extract_glossary(self) -> None:
        settings_input = build_settings_input(
            {
                "input_path": "/tmp/paper.pdf",
                "output_dir": "/tmp/output",
                "output_modes": ["dual"],
                "source_lang": "en",
                "target_lang": "zh-CN",
                "service": "openai",
                "no_auto_extract_glossary": True,
            }
        )

        self.assertTrue(settings_input["translation"]["no_auto_extract_glossary"])

    def test_build_settings_input_translates_table_text_by_default(self) -> None:
        settings_input = build_settings_input(
            {
                "input_path": "/tmp/paper.pdf",
                "output_dir": "/tmp/output",
                "output_modes": ["dual"],
                "source_lang": "en",
                "target_lang": "zh-CN",
                "service": "openai",
            }
        )

        self.assertTrue(settings_input["pdf"]["translate_table_text"])

    def test_build_settings_input_can_skip_table_text(self) -> None:
        settings_input = build_settings_input(
            {
                "input_path": "/tmp/paper.pdf",
                "output_dir": "/tmp/output",
                "output_modes": ["dual"],
                "source_lang": "en",
                "target_lang": "zh-CN",
                "service": "openai",
                "translate_table_text": False,
            }
        )

        self.assertFalse(settings_input["pdf"]["translate_table_text"])

    def test_settings_survive_cli_model_conversion(self) -> None:
        settings_input = build_settings_input(make_settings_payload())
        settings = CLIEnvSettingsModel.model_validate(
            settings_input
        ).to_settings_model()

        self.assertEqual(settings.translation.lang_in, "en")
        self.assertEqual(settings.translation.lang_out, "fr")
        self.assertEqual(settings.translation.output, str(Path("/private/tmp/output")))
        self.assertEqual(settings.translation.qps, 99)
        self.assertEqual(settings.translation.pool_max_workers, 77)
        self.assertTrue(settings.translation.no_auto_extract_glossary)
        self.assertEqual(settings.translation.primary_font_family, "sans-serif")

        self.assertTrue(settings.pdf.no_mono)
        self.assertFalse(settings.pdf.no_dual)
        self.assertEqual(settings.pdf.watermark_output_mode, "no_watermark")
        self.assertTrue(settings.pdf.ocr_workaround)
        self.assertFalse(settings.pdf.auto_enable_ocr_workaround)
        self.assertFalse(settings.pdf.translate_table_text)
        self.assertTrue(settings.pdf.skip_references)

    def test_auto_values_leave_pdf2zh_defaults_in_control(self) -> None:
        settings_input = build_settings_input(
            make_settings_payload(pool_size=0, font_family="auto")
        )
        translation = settings_input["translation"]

        self.assertNotIn("pool_max_workers", translation)
        self.assertNotIn("primary_font_family", translation)

    def test_output_modes_map_to_nested_pdf_settings(self) -> None:
        mono = build_settings_input(
            make_settings_payload(output_modes=["mono"])
        )["pdf"]
        both = build_settings_input(
            make_settings_payload(output_modes=["mono", "dual"])
        )["pdf"]

        self.assertFalse(mono["no_mono"])
        self.assertTrue(mono["no_dual"])
        self.assertFalse(both["no_mono"])
        self.assertFalse(both["no_dual"])

    def test_skip_last_pages_maps_to_nested_pdf_pages(self) -> None:
        fake_reader = SimpleNamespace(pages=[None] * 10)
        with patch("pdf2zh_next_service.PdfReader", return_value=fake_reader):
            settings_input = build_settings_input(
                make_settings_payload(skip_last_pages=3)
            )

        self.assertEqual(settings_input["pdf"]["pages"], "1-7")

    def test_service_details_remain_top_level(self) -> None:
        settings_input = build_settings_input(
            make_settings_payload(
                service="deepseek",
                llm_api={"model": "test-model", "apiKey": "test-key"},
            )
        )

        self.assertTrue(settings_input["deepseek"])
        self.assertEqual(
            settings_input["deepseek_detail"],
            {"deepseek_model": "test-model", "deepseek_api_key": "test-key"},
        )

    def test_text_check_bypass_skips_cid_checks_in_context(self) -> None:
        import babeldoc.format.pdf.high_level as babeldoc_high_level
        from babeldoc.format.pdf.document_il.midend import il_translator_llm_only
        from babeldoc.format.pdf.document_il.midend.paragraph_finder import (
            ParagraphFinder,
        )

        install_text_check_bypass()
        previous = set_text_checks_skipped(True)
        try:
            self.assertFalse(babeldoc_high_level.check_cid_char(object()))
            self.assertFalse(ParagraphFinder.check_cid_paragraph(object(), object()))
            self.assertFalse(il_translator_llm_only.is_cid_paragraph(object()))
        finally:
            set_text_checks_skipped(previous)

    def test_text_check_bypass_reaches_executor_threads(self) -> None:
        import babeldoc.format.pdf.high_level as babeldoc_high_level

        install_text_check_bypass()
        previous = set_text_checks_skipped(True)
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                self.assertFalse(
                    executor.submit(
                        babeldoc_high_level.check_cid_char,
                        object(),
                    ).result()
                )
        finally:
            set_text_checks_skipped(previous)

    def test_diagnose_service_error_classifies_openai_response_shape(self) -> None:
        diagnostics = diagnose_service_error("object has no attribute 'choices'")

        self.assertEqual(diagnostics[0]["code"], "llm_response_shape")
        self.assertEqual(diagnostics[0]["severity"], "error")

    def test_live_translator_test_returns_success(self) -> None:
        class Translator:
            def translate(self, text, ignore_cache=False, rate_limit_params=None):
                return f"{text} translated"

        result = run_live_translator_test(Translator(), timeout_seconds=1)

        self.assertTrue(result["ok"])
        self.assertEqual(result["message"], "Hello translated")

    def test_live_translator_test_returns_diagnostic_message_on_error(self) -> None:
        class Translator:
            def translate(self, text, ignore_cache=False, rate_limit_params=None):
                raise RuntimeError("401 Unauthorized")

        result = run_live_translator_test(Translator(), timeout_seconds=1)

        self.assertFalse(result["ok"])
        self.assertIn("401", result["message"])


if __name__ == "__main__":
    unittest.main()
