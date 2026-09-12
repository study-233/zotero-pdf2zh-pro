import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import openai

import pdf2zh_next_service as service
from babeldoc.format.pdf.translation_recovery import TranslationRecovery
from observability import TaskMetricsCollector
from test_observability import FakeClock
from test_openai_protocol import chat
from test_pdf2zh_next_service import make_settings_payload


class ServiceMetricsTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.payload = make_settings_payload(
            input_path=str(Path(self.directory) / "paper.pdf"), output_dir=str(Path(self.directory) / "output"),
            service="openaicompatible", qps=20, pool_size=100, target_lang="zh-CN", ocr=False,
            llm_api={"apiKey": "test-key", "apiUrl": "https://relay.invalid/v1", "model": "test-model", "apiProtocol": "auto"},
        )
        Path(self.payload["input_path"]).write_bytes(b"isolated-test-source")

    def client(self, handler):
        client = openai.OpenAI(api_key="test-key", base_url="https://relay.invalid/v1", max_retries=0,
                               http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        self.addCleanup(client.close)
        return self.enterContext(patch("pdf2zh_next.translator.translator_impl.openai.openai.OpenAI", return_value=client))

    def test_failed_initialization_probe_is_measured_before_configuration_returns(self):
        requests, snapshots = [], []

        def handler(request):
            requests.append(request)
            return httpx.Response(401, json={"error": {"message": "denied", "type": "authentication_error"}})

        self.client(handler)
        with self.assertRaises(openai.AuthenticationError):
            asyncio.run(service.translate_pdf_with_callbacks(self.payload, "probe-failure", metrics_callback=snapshots.append))
        self.assertEqual(len(requests), 1)
        metrics = snapshots[-1]
        self.assertEqual(metrics["requests"]["attempts"], 1)
        self.assertEqual(metrics["requests"]["byKind"]["initialization"]["failed"], 1)
        self.assertEqual(metrics["requests"]["byKind"]["initialization"]["statusCodes"], {"401": 1})
        self.assertEqual(metrics["requests"]["byKind"]["translation"]["attempts"], 0)
        self.assertIsNone(metrics["tokens"]["total"])
        self.assertIn("Initialization", metrics["stageDurations"])
        self.assertIn("Total", metrics["stageDurations"])
        self.assertFalse(service._TEXT_CHECK_TRANSLATION_LOCK.locked())

    def test_normal_service_initialization_probes_only_the_reused_main_translator(self):
        requests, snapshots = [], []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, json=chat("你好"))

        self.client(handler)

        def config(**kwargs):
            self.assertIs(kwargs["translator"], kwargs["term_extraction_translator"])
            return SimpleNamespace(**kwargs, cleanup_temp_files=lambda: None,
                                   raise_if_cancelled=lambda: None, cancel_translation=lambda: None)

        async def translate(config):
            yield {"type": "finish", "translate_result": object()}

        with patch("pdf2zh_next.high_level.BabelDOCConfig", side_effect=config), \
             patch.object(service, "download_all_fonts_async", return_value=None), \
             patch.object(service, "babeldoc_translate", side_effect=translate), \
             patch.object(service, "collect_output_files", return_value={}):
            asyncio.run(service.translate_pdf_with_callbacks(self.payload, "single-probe", metrics_callback=snapshots.append))
        self.assertEqual(len(requests), 1)
        metrics = snapshots[-1]
        self.assertEqual(metrics["requests"]["attempts"], 1)
        self.assertEqual(metrics["requests"]["byKind"]["initialization"]["succeeded"], 1)
        self.assertEqual(metrics["requests"]["byKind"]["translation"]["attempts"], 0)
        self.assertEqual(metrics["requests"]["byKind"]["review"]["attempts"], 0)
        self.assertEqual(metrics["tokens"]["total"], 120)
        self.assertEqual(metrics["tokens"]["byKind"]["initialization"]["total"], 120)

    def test_stage_and_total_timing_include_output_commit_and_database_close(self):
        clock, snapshots, databases = FakeClock(), [], []

        def config(settings, file, **kwargs):
            self.assertIsInstance(kwargs["metrics_collector"], TaskMetricsCollector)
            clock.advance(2)
            return SimpleNamespace(pool_max_workers=1, translator=SimpleNamespace(),
                                   cleanup_temp_files=lambda: None, raise_if_cancelled=lambda: None)

        async def fonts(progress_callback):
            clock.advance(5)

        async def translate(config):
            for stage, duration in (("Translate Paragraphs", 2), ("Document Layout", 3), ("Write PDF", 4)):
                yield {"type": "progress_start", "stage": stage, "stage_current": 0, "stage_total": 1}
                clock.advance(duration)
                yield {"type": "progress_end", "stage": stage, "stage_current": 1, "stage_total": 1}
            yield {"type": "finish", "translate_result": object()}

        def collect(*args):
            clock.advance(7)
            return {}

        def recovery(*args):
            value = TranslationRecovery(*args)
            databases.append(value)
            close = value.close

            def finish():
                clock.advance(11)
                close()

            value.close = finish
            return value

        def observed(metrics):
            snapshots.append(metrics)
            if "Total" in metrics["stageDurations"]:
                with self.assertRaises(sqlite3.ProgrammingError):
                    databases[0].connection.execute("SELECT 1")

        with patch.object(service.time, "monotonic", clock), \
             patch.object(service, "TaskMetricsCollector", side_effect=lambda **kw: TaskMetricsCollector(**kw, clock=clock)), \
             patch.object(service, "create_babeldoc_config", side_effect=config), \
             patch.object(service, "download_all_fonts_async", side_effect=fonts), \
             patch.object(service, "babeldoc_translate", side_effect=translate), \
             patch.object(service, "collect_output_files", side_effect=collect), \
             patch("babeldoc.format.pdf.translation_recovery.TranslationRecovery", side_effect=recovery):
            asyncio.run(service.translate_pdf_with_callbacks(self.payload, "stage-clock", metrics_callback=observed))
        stages = snapshots[-1]["stageDurations"]
        self.assertEqual(stages["Initialization"], 2)
        self.assertEqual(stages["Font Preparation"], 5)
        self.assertEqual(stages["Translate Paragraphs"], 2)
        self.assertEqual(stages["Document Layout"], 3)
        self.assertEqual(stages["Write PDF"], 4)
        self.assertEqual(stages["Finalize"], 18)
        self.assertEqual(stages["Total"], 34)


if __name__ == "__main__":
    unittest.main()
