from __future__ import annotations

import asyncio
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
from pdf2zh_next_service import create_font_progress_event
from pdf2zh_next_service import diagnose_service_error
from pdf2zh_next_service import explain_service_error
from pdf2zh_next_service import install_text_check_bypass
from pdf2zh_next_service import ProgressLogger
from pdf2zh_next_service import run_live_translator_test
from pdf2zh_next_service import set_text_checks_skipped
from pdf2zh_next_service import translate_pdf_with_callbacks
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
    def test_translation_prepares_fonts_before_babeldoc(self) -> None:
        sequence: list[str] = []
        events: list[dict] = []

        async def prepare_fonts(progress_callback):
            sequence.append("fonts")
            progress_callback("Check Fonts", 0, 2)
            progress_callback("Check Fonts", 2, 2)
            progress_callback("Download Fonts", 0, 100)
            progress_callback("Download Fonts", 100, 100)

        async def translate(_config):
            sequence.append("translation")
            yield {"type": "finish", "translate_result": SimpleNamespace()}

        config = SimpleNamespace(save_detailed_tracking=True)
        expected_files = {"dual": SimpleNamespace(filename="paper.dual.pdf")}
        payload = {
            "input_path": "/tmp/paper.pdf",
            "output_dir": "/tmp/output",
            "output_modes": ["dual"],
            "service": "openai",
        }

        with (
            patch("pdf2zh_next_service.create_runtime_settings", return_value=object()),
            patch("pdf2zh_next_service.create_babeldoc_config", return_value=config),
            patch(
                "pdf2zh_next_service.download_all_fonts_async",
                side_effect=prepare_fonts,
            ),
            patch("pdf2zh_next_service.babeldoc_translate", side_effect=translate),
            patch(
                "pdf2zh_next_service.collect_output_files",
                return_value=expected_files,
            ),
        ):
            result = asyncio.run(
                translate_pdf_with_callbacks(
                    payload,
                    "font-test",
                    progress_callback=events.append,
                )
            )

        self.assertEqual(sequence, ["fonts", "translation"])
        self.assertEqual(result.files, expected_files)
        self.assertEqual(
            [event["stage"] for event in events if "stage" in event],
            ["Check Fonts", "Check Fonts", "Download Fonts", "Download Fonts"],
        )

    def test_font_progress_and_download_diagnostics(self) -> None:
        start = create_font_progress_event("Download Fonts", 0, 200)
        update = create_font_progress_event("Download Fonts", 50, 200)
        end = create_font_progress_event("Download Fonts", 200, 200)

        self.assertEqual(start["type"], "progress_start")
        self.assertEqual(update["type"], "progress_update")
        self.assertEqual(update["stage_progress"], 25.0)
        self.assertEqual(update["overall_progress"], 0.0)
        self.assertEqual(end["type"], "progress_end")

        progress_logger = ProgressLogger("font-test")
        with self.assertLogs("zotero_pdf2zh_server.translate", level="INFO"):
            progress_logger.log(start)
            progress_logger.log(update)
            progress_logger.log(end)

        message = explain_service_error(
            "Font asset download failed for example.ttf: connection timeout"
        )
        diagnostics = diagnose_service_error(message)

        self.assertIn("字体资源下载失败", message)
        self.assertEqual(diagnostics[0]["code"], "font_asset_download")
        self.assertIn("重试任务", diagnostics[0]["suggestion"])

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

    def test_settings_contract_and_defaults(self) -> None:
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

        defaults = build_settings_input(
            make_settings_payload(pool_size=0, font_family="auto")
        )
        self.assertNotIn("pool_max_workers", defaults["translation"])
        self.assertNotIn("primary_font_family", defaults["translation"])

        default_payload = make_settings_payload()
        default_payload.pop("pool_size")
        default_payload.pop("no_auto_extract_glossary")
        default_settings = CLIEnvSettingsModel.model_validate(
            build_settings_input(default_payload)
        ).to_settings_model()
        self.assertEqual(default_settings.translation.pool_max_workers, 50)
        self.assertTrue(default_settings.translation.no_auto_extract_glossary)

    def test_text_check_bypass_in_context_and_executor(self) -> None:
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
            with ThreadPoolExecutor(max_workers=1) as executor:
                self.assertFalse(
                    executor.submit(
                        babeldoc_high_level.check_cid_char,
                        object(),
                    ).result()
                )
        finally:
            set_text_checks_skipped(previous)

    def test_service_diagnostics_and_live_probe(self) -> None:
        diagnostics = diagnose_service_error("object has no attribute 'choices'")

        self.assertEqual(diagnostics[0]["code"], "llm_response_shape")
        self.assertEqual(diagnostics[0]["severity"], "error")

        class SuccessfulTranslator:
            def translate(self, text, ignore_cache=False, rate_limit_params=None):
                return f"{text} translated"

        class FailingTranslator:
            def translate(self, text, ignore_cache=False, rate_limit_params=None):
                raise RuntimeError("401 Unauthorized")

        success = run_live_translator_test(SuccessfulTranslator(), timeout_seconds=1)
        failure = run_live_translator_test(FailingTranslator(), timeout_seconds=1)
        self.assertEqual(success, {"enabled": True, "ok": True, "message": "Hello translated"})
        self.assertFalse(failure["ok"])
        self.assertIn("401", failure["message"])


if __name__ == "__main__":
    unittest.main()
