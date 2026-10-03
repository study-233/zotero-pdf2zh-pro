"""Provider integration tests: no installed CLI, account or model calls required."""
import tempfile
import signal
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import server
from babeldoc.translator.request_budget import ParagraphRequestBudget
from babeldoc.translator.validation import InvalidTranslation
from codex_client import CodexError
from diagnostics import safe_fields
from observability import TaskMetricsCollector
from pdf2zh_next.config.translate_engine_model import CodexSettings, TERM_EXTRACTION_ENGINE_METADATA
from pdf2zh_next.translator import get_translator
from pdf2zh_next.translator.translator_impl.codex import CodexTranslator
from pdf2zh_next_service import create_runtime_settings, validate_service_config, run_live_translator_test
from provider_models import list_codex_models, ModelDiscoveryError
from test_pdf2zh_next_service import make_settings_payload
from text_translation import TextTranslationService, TextTranslationError
from translation_memory import TranslationMemory


class MemoryCache:
    def __init__(self, *args):
        self.values, self.params = {}, {}

    def add_params(self, key, value):
        self.params[key] = value

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value):
        self.values[key] = value

    def delete(self, key):
        self.values.pop(key, None)


class CodexProviderTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("pdf2zh_next.translator.base_translator.TranslationCache", MemoryCache))
        self.client = Mock()
        self.client.check_ready.return_value = {"model": "gpt-6-luna"}
        self.client.translate.return_value = SimpleNamespace(text="样本量有限。", usage={})
        self.factory = self.enterContext(patch(
            "pdf2zh_next.translator.translator_impl.codex.get_codex_client", return_value=self.client))
        self.payload = make_settings_payload(service="codex", llm_api={"model": "gpt-6-luna"})

    def translator(self, api=None):
        settings = create_runtime_settings({**self.payload, "llm_api": api or self.payload["llm_api"]})
        return CodexTranslator(settings, Mock())

    def test_settings_preserve_none_effort_and_support_term_extraction(self):
        settings = create_runtime_settings({**self.payload, "no_auto_extract_glossary": False,
            "llm_api": {"cliPath": "C:/Program Files/Codex/codex.exe", "reasoningEffort": "none"}})
        self.assertIsInstance(settings.translate_engine_settings, CodexSettings)
        self.assertEqual(settings.translate_engine_settings.codex_model, "gpt-6-luna")
        self.assertEqual(settings.translate_engine_settings.codex_reasoning_effort, "none")
        self.assertIs(settings.term_extraction_engine_settings, settings.translate_engine_settings)
        self.assertIn("Codex", [item.translate_engine_type for item in TERM_EXTRACTION_ENGINE_METADATA])
        with self.assertRaises(ValueError):
            create_runtime_settings({**self.payload, "llm_api": {"reasoningEffort": "unbounded"}})

    def test_factory_health_checks_config_without_generation(self):
        self.client.check_ready.return_value["reasoningEffort"] = "medium"
        translator = get_translator(create_runtime_settings(self.payload))
        self.assertIsInstance(translator, CodexTranslator)
        self.client.check_ready.assert_called_once_with("gpt-6-luna", None,
                                                       check_cancelled=translator.check_cancelled)
        self.client.translate.assert_not_called()
        self.assertEqual(translator.cache.params["reasoning_effort"], "medium")

    def test_validation_makes_only_one_explicit_live_request(self):
        for enabled in (False, True):
            self.client.reset_mock()
            result = validate_service_config({**self.payload, "live_test": enabled}, "codex-test")
            self.assertEqual(result.status, "ok")
            self.assertEqual(self.client.translate.call_count, int(enabled))
            self.assertIsNone(result.resolved_protocol)
            self.client.close.assert_not_called()

    def test_translation_and_llm_prompt_have_distinct_handling_and_keep_metrics(self):
        translator = self.translator({"model": "gpt-6-luna", "reasoningEffort": "none"})
        collector = TaskMetricsCollector(task_id="codex", provider="codex", model=translator.model)
        translator.set_metrics_collector(collector)
        self.client.translate.return_value.usage = {"prompt_tokens": 12, "completion_tokens": 4,
                                                  "cache_hit_tokens": 8, "cache_miss_tokens": 4}
        self.assertEqual(translator.translate("The sample size was limited."), "样本量有限。")
        self.assertIn("The sample size was limited.", self.client.translate.call_args.args[0])
        self.assertIn("translate it into fr", self.client.translate.call_args.args[0])
        translator.llm_translate("Return JSON", ignore_cache=True)
        self.assertEqual(self.client.translate.call_args.args[0], "Return JSON")
        self.assertEqual(self.client.translate.call_args.kwargs["reasoning_effort"], "none")
        self.assertEqual(collector.snapshot()["tokens"]["total"], 32)
        self.assertEqual(collector.snapshot()["requests"]["succeeded"], 2)
        self.assertEqual(translator.cache.params["reasoning_effort"], "none")

    def test_failed_cancelled_and_invalid_results_do_not_enter_cache(self):
        translator = self.translator()
        self.client.translate.side_effect = RuntimeError("failed")
        with self.assertRaises(RuntimeError):
            translator.translate("source")
        self.assertEqual(translator.cache.values, {})
        self.client.translate.side_effect = None
        cancelled = Mock(side_effect=RuntimeError("cancelled"))
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            translator.translate("source", rate_limit_params={"check_cancelled": cancelled})
        self.assertEqual(self.client.translate.call_count, 1)
        self.client.translate.return_value.text = ""
        with self.assertRaises(InvalidTranslation):
            translator.translate("source")
        self.assertEqual(translator.cache.values, {})

    def test_request_budget_and_unknown_usage_are_preserved(self):
        translator = self.translator()
        collector = TaskMetricsCollector(task_id="codex", provider="codex", model=translator.model)
        translator.set_metrics_collector(collector)
        budget = ParagraphRequestBudget().request([object()])
        on_attempt = Mock()
        translator.llm_translate("source", ignore_cache=True,
                                 rate_limit_params={"request_budget": budget, "on_attempt": on_attempt})
        self.assertEqual(budget.attempts, 1)
        on_attempt.assert_called_once()
        self.assertIsNone(collector.snapshot()["tokens"]["total"])

    def test_connection_timeout_is_applied_and_restored(self):
        translator = self.translator()
        self.client.translate.side_effect = TimeoutError("timeout")
        result = run_live_translator_test(translator, timeout_seconds=3)
        self.assertFalse(result["ok"])
        self.assertEqual(self.client.translate.call_args.kwargs["timeout"], 3)
        self.assertEqual(translator.timeout, 120)
        self.assertEqual(self.client.translate.call_count, 1)

    def test_model_discovery_endpoint_includes_reasoning_and_health_capability(self):
        details = [{"id": "gpt-6-luna", "displayName": "GPT-6 Luna", "defaultReasoningEffort": "medium",
                    "supportedReasoningEfforts": ["none", "medium"]}]
        with patch("provider_models.get_codex_client", return_value=self.client):
            self.client.list_models.return_value = details
            client = server.create_app().test_client()
            response = client.post("/list-models", json={"service": "codex", "cliPath": "codex"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json["modelDetails"], details)
            self.assertEqual(response.json["models"], ["gpt-6-luna"])
        with patch.object(server, "build_workspace_health", return_value={}), \
             patch.object(server, "build_task_stats", return_value={}), \
             patch.object(server, "TASK_MANAGER", SimpleNamespace(_queue_blocked=False)):
            self.assertTrue(client.get("/health").json["capabilities"]["codexCli"])
        with self.assertRaises(ModelDiscoveryError):
            list_codex_models({"cliPath": {"bad": True}})

    def test_selection_modes_use_codex_without_closing_shared_client(self):
        with tempfile.TemporaryDirectory() as directory:
            service = TextTranslationService(timeout=1)
            self.addCleanup(service.executor.shutdown)
            memory = TranslationMemory(Path(directory) / "memory.sqlite3")
            data = {"text": "sample", "source": "en", "target": "zh-CN", "service": "codex",
                    "llm_api": {"model": "gpt-6-luna"}, "documentFingerprint": "paper",
                    "context": "The sample size was limited.", "selectionProvider": "profile"}
            answers = {"translate": "样本", "lookup": "样本", "explain": "这是研究中的样本。",
                       "context": '{"meaning":"样本","explanation":"指本研究的观测对象。"}',
                       "dictionary": '{"headword":"sample","senses":[{"chinese":"样本","english":"sample","pos":"n.","examples":[]}]}'}
            for mode, answer in answers.items():
                with self.subTest(mode=mode):
                    self.client.translate.return_value.text = answer
                    result = service.translate({**data, "mode": mode}, memory)
                    self.assertEqual(result["status"], "ok")
            self.assertEqual(self.client.translate.call_count, 5)
            self.client.close.assert_not_called()
            self.assertNotEqual(service._key(data, data["context"]),
                                service._key({**data, "llm_api": {"model": "gpt-6-luna", "reasoningEffort": "none"}}, data["context"]))

    def test_selection_quota_error_is_safe_and_never_cached_or_replayed(self):
        service = TextTranslationService(timeout=1)
        self.addCleanup(service.executor.shutdown)
        error = CodexError("codex_quota_exhausted", "safe message", 429)
        self.client.translate.side_effect = error
        with tempfile.TemporaryDirectory() as directory:
            memory = TranslationMemory(Path(directory) / "memory.sqlite3")
            data = {"text": "sample", "service": "codex", "llm_api": {"model": "gpt-6-luna"}}
            with self.assertRaises(TextTranslationError) as caught:
                service.translate(data, memory)
            self.assertEqual(caught.exception.code, "codex_quota_exhausted")
            self.assertEqual(caught.exception.status, 429)
            self.assertEqual(caught.exception.message, "safe message")
            self.assertEqual(self.client.translate.call_count, 1)
            self.assertEqual(service.cache, {})
            self.client.close.assert_not_called()

    def test_codex_error_message_reaches_reader_and_diagnostics_keep_only_fixed_labels(self):
        error = TextTranslationError("codex_not_installed", 400, "请安装 Codex CLI。")
        with patch.object(server.TEXT_TRANSLATOR, "translate", side_effect=error):
            response = server.create_app().test_client().post("/translate-text", json={"text": "sample"})
        self.assertEqual(response.json["message"], "请安装 Codex CLI。")
        self.assertEqual(response.json["code"], "codex_not_installed")
        data = {"providerCode": "codex_quota_exhausted", "errorType": "CodexError",
                "protocol": "codex_app_server", "message": "private paper", "cliPath": "/private/path"}
        self.assertEqual(safe_fields(data), {key: data[key] for key in ("providerCode", "errorType", "protocol")})
        self.assertNotIn("providerCode", safe_fields({"providerCode": "codex_secret-paper"}))

    def test_service_shutdown_cancels_active_codex_request_and_prevents_cache_write(self):
        service = TextTranslationService(timeout=2)
        started = threading.Event()

        def pending(_prompt, **options):
            started.set()
            self.assertTrue(service.closed.wait(1))
            options["check_cancelled"]()
            self.fail("shutdown should cancel the request")

        self.client.translate.side_effect = pending
        with tempfile.TemporaryDirectory() as directory, ThreadPoolExecutor(max_workers=1) as caller:
            memory = TranslationMemory(Path(directory) / "memory.sqlite3")
            data = {"text": "sample", "service": "codex", "llm_api": {"model": "gpt-6-luna"}}
            future = caller.submit(service.translate, data, memory)
            try:
                self.assertTrue(started.wait(1))
                service.close()
                with self.assertRaisesRegex(TextTranslationError, "selection_unavailable"):
                    future.result(timeout=1)
                with self.assertRaisesRegex(TextTranslationError, "selection_unavailable"):
                    service.translate(data, memory)
                self.assertFalse(service.cache)
            finally:
                service.close()

    def test_sigterm_closes_owned_resources_and_restores_previous_handler(self):
        args = SimpleNamespace(data_dir=None, log_level="INFO", log_file=None, host="127.0.0.1", port=8891)
        tasks, glossary, selection = Mock(), Mock(), Mock()
        installed = {}

        def register(signum, handler):
            installed[signum] = handler

        def serve(**_kwargs):
            installed[signal.SIGTERM](signal.SIGTERM, None)

        with patch.object(server, "parse_args", return_value=args), \
             patch.object(server, "configure_runtime_paths"), patch.object(server, "configure_logging"), \
             patch.object(server, "TASK_MANAGER", tasks), patch.object(server, "GLOSSARY_MANAGER", glossary), \
             patch.object(server, "TEXT_TRANSLATOR", selection), \
             patch.object(server, "close_codex_clients") as close_clients, \
             patch.object(server.signal, "getsignal", return_value=signal.SIG_DFL), \
             patch.object(server.signal, "signal", side_effect=register), \
             patch.object(server.app, "run", side_effect=serve):
            with self.assertRaises(SystemExit) as caught:
                server.main()
            self.assertEqual(caught.exception.code, 128 + signal.SIGTERM)
            selection.close.assert_called_once()
            close_clients.assert_called_once()
            tasks.close.assert_called_once()
            glossary.close.assert_called_once()
            self.assertEqual(installed[signal.SIGTERM], signal.SIG_DFL)


if __name__ == "__main__":
    unittest.main()
