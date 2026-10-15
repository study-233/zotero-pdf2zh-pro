"""Provider integration tests: no installed CLI, account or model calls required."""
import tempfile
import asyncio
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
        self.client.session.return_value = self.client
        self.client.check_ready.return_value = {"model": "gpt-6-luna"}
        self.client.translate.return_value = SimpleNamespace(text="样本量有限。", usage={})
        self.factory = self.enterContext(patch(
            "pdf2zh_next.translator.translator_impl.codex.get_codex_client", return_value=self.client))
        self.payload = make_settings_payload(service="codex", llm_api={"model": "gpt-6-luna"})

    def translator(self, api=None):
        settings = create_runtime_settings({**self.payload, "llm_api": api or self.payload["llm_api"]})
        return CodexTranslator(settings, Mock())

    def test_failed_and_cancelled_requests_retain_usage_without_success(self):
        for error in (asyncio.CancelledError(), CodexError("codex_request_failed", "failed", 502)):
            with self.subTest(error=type(error).__name__):
                translator = self.translator()
                collector = TaskMetricsCollector(task_id="codex", provider="codex", model=translator.model)
                translator.set_metrics_collector(collector)
                def fail(*args, **options):
                    options["on_usage"]({"prompt_tokens": 100, "completion_tokens": 50})
                    options["on_usage"]({"prompt_tokens": 100, "completion_tokens": 50})
                    raise error
                self.client.translate.side_effect = fail
                with self.assertRaises(type(error)):
                    translator.llm_translate("source")
                metrics = collector.snapshot()
                self.assertEqual(metrics["tokens"]["total"], 150)
                self.assertEqual(metrics["tokens"]["availability"], "partial")
                self.assertEqual(metrics["requests"]["succeeded"], 0)
                self.assertEqual(metrics["requests"]["cancelled"], int(isinstance(error, asyncio.CancelledError)))
                self.assertEqual(metrics["requests"]["failed"], int(not isinstance(error, asyncio.CancelledError)))
                self.assertEqual(metrics["requests"]["active"], 0)
                self.assertFalse(translator.cache.values)

    def test_fatal_error_stops_shared_task_calls_but_not_a_new_task(self):
        for code in ("codex_quota_exhausted", "codex_not_logged_in", "codex_model_unavailable",
                     "codex_isolation_failed", "codex_invalid_reasoning", "codex_incompatible"):
            with self.subTest(code=code):
                self.client.reset_mock()
                main, terms = self.translator(), self.translator()
                terms.request_state = main.request_state
                self.client.translate.side_effect = CodexError(code, "fatal")
                with self.assertRaises(CodexError):
                    main.llm_translate("first", ignore_cache=True)
                self.client.translate.side_effect = None
                for engine, kind in ((main, "translation"), (terms, "translation"), (main, "review")):
                    with self.assertRaises(CodexError) as caught:
                        engine.llm_translate("later", ignore_cache=True, rate_limit_params={"metric_kind": kind})
                    self.assertEqual(caught.exception.code, code)
                self.assertEqual(self.client.translate.call_count, 1)
                self.assertEqual(self.translator().llm_translate("new task"), "样本量有限。")

    def test_nonfatal_errors_do_not_stop_later_requests(self):
        for code in ("codex_timeout", "codex_request_failed", "codex_process_exited"):
            with self.subTest(code=code):
                translator = self.translator()
                self.client.translate.side_effect = CodexError(code, "temporary")
                with self.assertRaises(CodexError):
                    translator.llm_translate("first")
                self.client.translate.side_effect = None
                self.assertEqual(translator.llm_translate("second"), "样本量有限。")

    def test_codex_qps_and_pool_are_capped_before_readiness_without_raising_user_limits(self):
        from pdf2zh_next.translator import get_term_translator
        for requested, expected in ((10, 2), (1, 1)):
            with self.subTest(requested=requested):
                settings = create_runtime_settings({**self.payload, "qps": requested, "pool_size": requested,
                                                   "no_auto_extract_glossary": False})
                def ready(*args, **kwargs):
                    self.assertEqual(settings.translation.qps, expected)
                    self.assertEqual(settings.translation.pool_max_workers, expected)
                    return {"reasoningEffort": "medium"}
                self.client.check_ready.side_effect = ready
                translator = get_translator(settings)
                self.assertEqual(translator.rate_limiter.max_qps, expected)
                settings.translation.term_qps = 1
                settings.translation.term_pool_max_workers = 1
                terms = get_term_translator(settings)
                self.assertEqual(terms.rate_limiter.max_qps, 1)
                self.assertEqual(settings.translation.term_pool_max_workers, 1)

    def test_dedicated_term_translator_shares_task_stop_state(self):
        from pdf2zh_next.high_level import create_babeldoc_config
        settings = create_runtime_settings({**self.payload, "no_auto_extract_glossary": False})
        settings.translation.term_qps = 1
        with patch("pdf2zh_next.high_level.BabelDOCConfig", side_effect=lambda **kwargs: SimpleNamespace(**kwargs)):
            config = create_babeldoc_config(settings, Path("/tmp/source.pdf"))
        self.assertIsNot(config.translator, config.term_extraction_translator)
        self.assertIs(config.translator.request_state, config.term_extraction_translator.request_state)

    def test_selection_stream_uses_the_same_usage_and_completion_accounting(self):
        translator = self.translator()
        collector = TaskMetricsCollector(task_id="selection", provider="codex", model=translator.model)
        translator.set_metrics_collector(collector)
        def respond(*args, **options):
            options["on_usage"]({"prompt_tokens": 100, "completion_tokens": 50})
            options["on_text"]("译文")
            return SimpleNamespace(text="译文", usage={"prompt_tokens": 100, "completion_tokens": 50})
        self.client.translate.side_effect = respond
        deltas = []
        self.assertEqual(translator.selection_stream("source", deltas.append, threading.Event()), "译文")
        self.assertEqual(deltas, ["译文"])
        self.assertEqual(collector.snapshot()["tokens"]["total"], 150)
        self.assertEqual(collector.snapshot()["requests"]["succeeded"], 1)

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

    def test_proxy_settings_reach_all_provider_entry_points_without_affecting_cache_identity(self):
        api = {"model": "gpt-6-luna", "proxyMode": "manual", "proxyUrl": "http://127.0.0.1:7897/"}
        proxied = self.translator(api)
        self.factory.assert_called_with(None, proxy_mode="manual", proxy_url="http://127.0.0.1:7897")
        inherited = self.translator()
        self.assertEqual(proxied.cache.params, inherited.cache.params)
        validate_service_config({**self.payload, "llm_api": api, "live_test": False}, "proxy-test")
        self.factory.assert_called_with(None, proxy_mode="manual", proxy_url="http://127.0.0.1:7897")
        with tempfile.TemporaryDirectory() as directory:
            service = TextTranslationService(timeout=1)
            self.addCleanup(service.close)
            data = {"text": "sample", "service": "codex", "llm_api": api}
            service.translate(data, TranslationMemory(Path(directory) / "memory.sqlite3"))
            self.factory.assert_called_with(None, proxy_mode="manual", proxy_url="http://127.0.0.1:7897")
            self.assertEqual(service._key(data, ""), service._key({**data, "llm_api": {"model": "gpt-6-luna"}}, ""))
        with patch("provider_models.get_codex_client", return_value=self.client) as factory:
            self.client.list_models.return_value = []
            list_codex_models({"proxyMode": "direct", "proxyUrl": "ignored"})
            factory.assert_called_with(None, proxy_mode="direct", proxy_url="ignored")
        with self.assertRaises(CodexError):
            self.translator({**api, "proxyUrl": "http://user:password@localhost:7897"})

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

    def test_live_codex_error_retains_code_in_http_response(self):
        self.client.translate.side_effect = CodexError("codex_not_logged_in", "请登录", 401)
        response = server.create_app().test_client().post("/validate-config", json={
            "service": "codex", "llm_api": {"model": "gpt-6-luna"}, "liveTest": True})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json["code"], "codex_not_logged_in")
        self.assertEqual(self.client.translate.call_count, 1)

    def test_validation_cancellation_reaches_readiness_before_generation(self):
        from codex_discovery import cancel_discovery
        request_id = "cancel-validation-123"
        def ready(*args, check_cancelled):
            cancel_discovery(request_id)
            check_cancelled()
        self.client.check_ready.side_effect = ready
        response = server.create_app().test_client().post("/validate-config", json={
            "service": "codex", "llm_api": {"model": "gpt-6-luna"},
            "liveTest": True, "requestId": request_id})
        self.assertEqual(response.status_code, 499)
        self.assertEqual(response.json["code"], "codex_cancelled")
        self.client.translate.assert_not_called()

    def test_model_discovery_endpoint_includes_reasoning_and_health_capability(self):
        details = [{"id": "gpt-6-luna", "displayName": "GPT-6 Luna", "defaultReasoningEffort": "medium",
                    "supportedReasoningEfforts": ["none", "medium"]}]
        with patch("provider_models.get_codex_client", return_value=self.client):
            self.client.model_catalog.return_value = (details, {"path": "/test/codex", "source": "desktop", "version": "0.160.0", "compatible": True, "attempts": []})
            client = server.create_app().test_client()
            response = client.post("/list-models", json={"service": "codex", "cliPath": "codex"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json["modelDetails"], details)
            self.assertEqual(response.json["models"], ["gpt-6-luna"])
            self.assertEqual(response.json["codexRuntime"]["source"], "desktop")
        with patch.object(server, "build_workspace_health", return_value={}), \
             patch.object(server, "build_task_stats", return_value={}), \
             patch.object(server, "TASK_MANAGER", SimpleNamespace(_queue_blocked=False)):
            self.assertTrue(client.get("/health").json["capabilities"]["codexCli"])
        with self.assertRaises(ModelDiscoveryError):
            list_codex_models({"cliPath": {"bad": True}})

    def test_discovery_endpoint_is_local_only_and_preserves_typed_errors(self):
        app = server.create_app().test_client()
        runtime = {"path": "/test/codex", "source": "desktop", "version": "0.162.0-alpha.17.2", "compatible": True, "attempts": []}
        with patch("server.get_codex_client", return_value=self.client):
            self.client.discover.return_value = runtime
            response = app.post("/codex/discover", json={"cliPath": "", "force": True})
            self.assertEqual(response.json["codexRuntime"], runtime)
            self.client.discover.assert_called_once_with(force=True, check_cancelled=None)
            self.client.translate.assert_not_called()
            self.client.list_models.assert_not_called()
            error = CodexError("codex_dependency_missing", "启动器找不到 Node")
            error.attempts = [{"path": "/test/codex", "source": "path", "code": error.code, "message": str(error)}]
            self.client.discover.side_effect = error
            response = app.post("/codex/discover", json={})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json["code"], error.code)
            self.assertEqual(len(response.json["attempts"]), 1)
        for body in ([], {"force": "yes"}, {"cliPath": []}, {"requestId": "bad id"}):
            self.assertEqual(app.post("/codex/discover", json=body).status_code, 400)
        self.assertEqual(app.post("/codex/discover/cancel", json={"requestId": "cancel-endpoint-test"}).status_code, 200)
        self.assertEqual(app.post("/codex/discover", json={"requestId": "cancel-endpoint-test"}).status_code, 499)

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
