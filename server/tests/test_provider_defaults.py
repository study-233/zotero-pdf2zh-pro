"""Endpoint-aware defaults and bounded compatibility negotiation, without credentials."""
import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import httpx
import openai

import test_openai_protocol as protocols
from test_provider_contracts import runtime
from test_cache_namespace import NoopRateLimiter
from pdf2zh_pro.translator import prepare_translator
from pdf2zh_pro.translator.reasoning_mode import (
    model_capabilities, apply_reasoning_mode, remember_capabilities, cached_capabilities,
)
from pdf2zh_pro.translator.openai import OpenAITranslator


class ProviderDefaultsTests(unittest.TestCase):
    def translator(self, handler, *, endpoint="https://relay.invalid/v1", model="gpt-6-luna",
                   mode="low", protocol="auto", options=None):
        self.requests = []
        def capture(request):
            self.requests.append((str(request.url), json.loads(request.content)))
            return handler(request)
        sdk = openai.OpenAI(api_key="TEST_KEY", base_url=endpoint, max_retries=0,
                            http_client=httpx.Client(transport=httpx.MockTransport(capture)))
        self.addCleanup(sdk.close)
        with patch("openai.OpenAI", return_value=sdk):
            return OpenAITranslator(runtime("openai", apiUrl=endpoint, model=model,
                apiProtocol=protocol, reasoningMode=mode, requestOptions=options or {}), NoopRateLimiter())

    def test_responses_first_low_is_fixed_for_the_task(self):
        engine = self.translator(lambda r: httpx.Response(200, json=protocols.response()))
        engine.health_check()
        engine.do_translate("paper")
        self.assertEqual([u for u, _ in self.requests], ["https://relay.invalid/v1/responses"] * 2)
        self.assertTrue(all(b["reasoning"] == {"effort": "low"} for _, b in self.requests))
        self.assertEqual(engine.reasoning_status()["effective"], "low")

    def test_responses_rejection_falls_back_once_preserving_low_or_off(self):
        for mode, effort in [("low", "low"), ("off", "none")]:
            engine = self.translator(lambda r: httpx.Response(404, json={"error": "Unknown endpoint"})
                if r.url.path.endswith("responses") else httpx.Response(200, json=protocols.chat()), mode=mode)
            engine.health_check()
            engine.do_translate("paper")
            self.assertEqual([u.rsplit("/v1/", 1)[-1] for u, _ in self.requests],
                             ["responses", "chat/completions", "chat/completions"])
            self.assertEqual(self.requests[-1][1]["reasoning_effort"], effort)
            self.assertTrue(engine.reasoning_status()["protocolFallback"])

    def test_unsupported_automatic_low_retries_same_protocol_once(self):
        def handler(request):
            body = json.loads(request.content)
            if "reasoning" in body:
                return httpx.Response(400, json={"error": {"message": "Unsupported parameter: reasoning.effort"}})
            return httpx.Response(200, json=protocols.response())
        engine = self.translator(handler, model="future-model")
        engine.health_check()
        engine.do_translate("paper")
        self.assertEqual(len(self.requests), 3)
        self.assertTrue(all(u.endswith("responses") for u, _ in self.requests))
        self.assertNotIn("reasoning", self.requests[-1][1])
        self.assertEqual(engine.reasoning_status()["effective"], "default")

    def test_off_and_explicit_parameters_are_never_silently_dropped(self):
        for mode, options in [("off", {}), ("low", {"reasoning_effort": "low"})]:
            engine = self.translator(lambda r: httpx.Response(400, json={"error": {"message": "Unsupported reasoning effort"}}),
                                     mode=mode, options=options, protocol="responses")
            with self.assertRaises(openai.BadRequestError): engine.health_check()
            self.assertEqual(len(self.requests), 1)

    def test_automatic_protocol_preserves_off_when_only_chat_accepts_it(self):
        engine = self.translator(lambda r: httpx.Response(400, json={"error": {"message": "Unsupported reasoning effort none"}})
            if r.url.path.endswith("responses") else httpx.Response(200, json=protocols.chat()), mode="off")
        engine.health_check()
        self.assertEqual(engine.resolved_protocol, "chat_completions")
        self.assertEqual(self.requests[-1][1]["reasoning_effort"], "none")
        self.assertEqual(engine.reasoning_status()["effective"], "off")

    def test_selection_stream_uses_negotiated_low_and_only_emits_translation(self):
        from text_translation import SelectionCancellation
        def handler(request):
            if request.url.path.endswith("responses"):
                return httpx.Response(404, json={"error": "Unknown endpoint"})
            if not json.loads(request.content).get("stream"):
                return httpx.Response(200, json=protocols.chat())
            events = [{"choices":[{"index":0,"delta":{"reasoning_content":"private reasoning"},"finish_reason":None}]},
                      {"choices":[{"index":0,"delta":{"content":"准确译文"},"finish_reason":None}]},
                      {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}]
            return httpx.Response(200, headers={"content-type":"text/event-stream"},
                text="".join("data: "+json.dumps(e)+"\n\n" for e in events)+"data: [DONE]\n\n")
        engine = self.translator(handler)
        engine.health_check()
        seen = []
        self.assertEqual(engine.selection_stream("paper", seen.append, SelectionCancellation()), "准确译文")
        self.assertEqual(seen, ["准确译文"])
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(self.requests[-1][1]["reasoning_effort"], "low")

    def test_all_compatible_presets_use_the_same_negotiation(self):
        cases = [
            ("openai", "https://api.openai.com/v1", "gpt-6-luna", "responses"),
            ("openai", "https://openrouter.ai/api/v1", "openai/gpt-6-luna", "responses"),
            ("deepseek", "https://api.deepseek.com/v1", "deepseek-flash", "responses"),
            ("mimo", "https://api.xiaomimimo.com/v1", "mimo-v2.6-pro", "responses"),
            ("zhipu", "https://open.bigmodel.cn/api/paas/v4", "glm-5.3-flash", "responses"),
            ("gemini", "https://generativelanguage.googleapis.com/v1beta/openai", "gemini-3.8-flash", "chat_completions"),
            ("aliyundashscope", "https://w.cn-beijing.maas.aliyuncs.com/compatible-mode/v1", "qwen3.8-flash", "responses"),
            ("siliconflow", "https://api.siliconflow.cn/v1", "deepseek-ai/DeepSeek-V4-Flash", "chat_completions"),
        ]
        for service, endpoint, model, protocol in cases:
            with self.subTest(service=service):
                calls = []
                def handler(request):
                    calls.append(request)
                    return httpx.Response(200, json=protocols.response() if request.url.path.endswith("responses") else protocols.chat())
                sdk = openai.OpenAI(api_key="TEST_KEY", base_url=endpoint,
                    http_client=httpx.Client(transport=httpx.MockTransport(handler)))
                self.addCleanup(sdk.close)
                with patch("openai.OpenAI", return_value=sdk):
                    engine = prepare_translator(runtime(service, apiUrl=endpoint, model=model, apiProtocol="auto", reasoningMode="low"))
                self.assertEqual(engine.resolved_protocol, protocol)
                self.assertEqual(len(calls), 1)
                if service == "zhipu": self.assertEqual(calls[0].url.path, "/api/v1/responses")
                if service == "siliconflow":
                    self.assertNotIn("reasoning_effort", json.loads(calls[0].content))
                    self.assertEqual(engine.reasoning_status()["effective"], "default")
                if service == "mimo": self.assertEqual(engine.reasoning_status()["effective"], "enabled")

    def test_zhipu_fallback_changes_official_base_but_not_custom_paths(self):
        engine = self.translator(lambda r: httpx.Response(400, json={"error": {"code": "unsupported_protocol"}})
            if r.url.path.endswith("responses") else httpx.Response(200, json=protocols.chat()),
            endpoint="https://open.bigmodel.cn/api/paas/v4", model="glm-5.3-flash")
        engine.health_check()
        self.assertEqual([u for u, _ in self.requests], ["https://open.bigmodel.cn/api/v1/responses",
                         "https://open.bigmodel.cn/api/paas/v4/chat/completions"])
        self.assertEqual(self.requests[-1][1]["reasoning_effort"], "low")

    def test_openrouter_preserves_native_reasoning_and_detects_conflicts(self):
        engine = self.translator(lambda r: httpx.Response(200, json=protocols.chat()),
            endpoint="https://openrouter.ai/api/v1", protocol="chat_completions",
            options={"reasoning": {"effort": "low", "exclude": True}})
        engine.health_check()
        self.assertEqual(self.requests[-1][1]["reasoning"], {"effort": "low", "exclude": True})
        with self.assertRaisesRegex(ValueError, "冲突"):
            apply_reasoning_mode({"reasoning_effort": "high"}, "low", "gpt-6-luna", "responses")

    def test_catalog_capabilities_are_scoped_to_endpoint_key_and_model(self):
        self.assertTrue(model_capabilities("Qwen/Qwen3.7-Flash", "https://api.commandcode.ai/provider/v1")["supportsReasoningOff"])
        self.assertIsNone(model_capabilities("Qwen/Qwen3.7-Flash", "https://other.invalid/v1")["supportsReasoningOff"])
        base = "https://capabilities.invalid/v1"
        remember_capabilities(base, "KEY_A", [{"id": "future", "supported_endpoints": ["/v1/chat/completions"],
            "reasoning": {"supported_efforts": ["low", "none"], "mandatory": False}}])
        caps = cached_capabilities("future", base, "KEY_A")
        self.assertTrue(caps["supportsReasoningOff"])
        self.assertEqual(caps["supportedProtocols"], ["chat_completions"])
        self.assertIsNone(cached_capabilities("future", base, "KEY_B")["supportsReasoningOff"])
        self.assertIsNone(cached_capabilities("future", base + "/other", "KEY_A")["supportsReasoningOff"])
        engine = self.translator(lambda r: httpx.Response(200, json=protocols.response()), mode="default")
        self.assertNotIn("reasoning", engine._options("responses"))

    def test_native_ollama_and_claude_controls(self):
        from pdf2zh_pro.translator.ollama import OllamaTranslator
        from pdf2zh_pro.translator.claudecode import ClaudeCodeTranslator
        for values, expected in [(["low", "medium"], "low"), ([True, False], True), ([], None)]:
            client = Mock()
            client.show.return_value = {"thinking": {"values": values}}
            with patch("ollama.Client", return_value=client):
                engine = OllamaTranslator(runtime("ollama", model="local", apiUrl="http://localhost:11434", reasoningMode="low"), NoopRateLimiter())
            self.assertEqual(engine.thinking_options, {"think": expected} if expected is not None else {})
        with patch("subprocess.run", return_value=NS(returncode=0, stdout="--effort", stderr="")):
            engine = ClaudeCodeTranslator(runtime("claudecode", model="sonnet", reasoningMode="low"), NoopRateLimiter())
        self.assertEqual(engine.effort_args, ["--effort", "low"])
        with self.assertRaisesRegex(ValueError, "必须开启"):
            apply_reasoning_mode({}, "off", "z-ai/glm-5.3-flash", "responses")

    def test_request_evidence_retains_reasoning_choice(self):
        from diagnostics import DiagnosticStore, safe_fields
        from observability import TaskMetricsCollector
        engine = self.translator(lambda r: httpx.Response(200, json=protocols.response()))
        metrics = TaskMetricsCollector(task_id="defaults", provider="openai", model="gpt-6-luna")
        metrics.evidence = Mock()
        engine.set_metrics_collector(metrics)
        engine.health_check()
        values = metrics.evidence.request_finished.call_args.kwargs
        self.assertEqual(values["reasoningSetting"], "low")
        self.assertEqual(values["reasoningRequested"], "low")
        store = DiagnosticStore()
        store.record("request", {"reasoningSetting": "low", "reasoningRequested": "low", "apiKey": "PRIVATE_KEY"})
        exported = store.export()["records"][0]
        self.assertEqual(exported["reasoningSetting"], "low")
        self.assertNotIn("PRIVATE_KEY", json.dumps(exported))
        self.assertNotIn("reasoningSetting", safe_fields({"reasoningSetting": "PRIVATE_KEY"}))
