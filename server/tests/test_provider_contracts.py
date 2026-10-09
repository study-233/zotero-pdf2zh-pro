"""Offline provider contracts from docs; no real credentials or model calls."""
import json
import subprocess
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import httpx
import openai
from pdf2zh_next.translator import get_translator
from pdf2zh_next_service import create_runtime_settings, require_supported_service, SERVICE_FIELD_MAP
from provider_models import list_provider_models, MODEL_ENDPOINTS, ModelDiscoveryError
from test_pdf2zh_next_service import make_settings_payload
from test_openai_protocol import chat
from test_cache_namespace import NoopRateLimiter

IMPL = "pdf2zh_next.translator.translator_impl."


def runtime(service, **api):
    return create_runtime_settings(make_settings_payload(service=service, source_lang="en", target_lang="zh-CN",
        llm_api={"apiKey": "TEST_KEY", "apiProtocol": "chat_completions", **api}))


class ProviderContractTests(unittest.TestCase):
    def test_openrouter_stream_uses_compatible_url_auth_and_model(self):
        from pdf2zh_next.translator.translator_impl.openai import OpenAITranslator
        from text_translation import SelectionCancellation
        calls = []
        def capture(request):
            calls.append(request)
            chunks = [
                {"id": "r", "object": "chat.completion.chunk", "created": 0, "model": "vendor/model", "choices": [{"index": 0, "delta": {"content": "译文"}, "finish_reason": None}]},
                {"id": "r", "object": "chat.completion.chunk", "created": 0, "model": "vendor/model", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            ]
            body = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"
            return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
        endpoint = "https://openrouter.ai/api/v1"
        sdk = openai.OpenAI(api_key="TEST_KEY", base_url=endpoint, max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(capture)))
        self.addCleanup(sdk.close)
        with patch("openai.OpenAI", return_value=sdk):
            translator = OpenAITranslator(runtime("openai", apiUrl=endpoint, model="vendor/model"), NoopRateLimiter())
        deltas = []
        self.assertEqual(translator.selection_stream("Hello", deltas.append, SelectionCancellation()), "译文")
        self.assertEqual(deltas, ["译文"])
        self.assertEqual(str(calls[0].url), endpoint + "/chat/completions")
        self.assertEqual(calls[0].headers["authorization"], "Bearer TEST_KEY")
        body = json.loads(calls[0].content)
        self.assertEqual(body["model"], "vendor/model")
        self.assertTrue(body["stream"])

    def test_retired_selection_services_are_rejected_before_generation(self):
        from text_translation import validate_text_request, TextTranslationError
        for service in ["zhipu", "grok", "groq", "modelscope", "qwenmt", "azureopenai", "azure", "xinference", "anythingllm"]:
            with self.subTest(service=service), self.assertRaises(TextTranslationError) as error:
                validate_text_request({"text": "Hello", "service": service})
            self.assertEqual(error.exception.code, "invalid_config")
            self.assertIn("已移除", error.exception.message)

    def test_removed_and_unknown_services_never_select_default_engine(self):
        import server
        client = server.create_app().test_client()
        self.assertEqual(len(SERVICE_FIELD_MAP), 9)
        for service in ["OpenAI_Compatible", "tencentmechinetranslation", "Dify", "SiliconFlow_Free", "zhipu", "grok", "groq", "modelscope", "qwenmt", "azureopenai", "azure", "xinference", "anythingllm", "unknown", "", None, 1]:
            with self.subTest(service=service), patch("server.validate_service_config") as validate:
                with self.assertRaises(ValueError): require_supported_service(service)
                self.assertEqual(client.post("/validate-config", json={"service": service}).status_code, 400)
                validate.assert_not_called()
                with self.assertRaises(ValueError): runtime(service)
        for service in ["openaicompatible", "tencentmechinetranslation", "dify", "siliconflowfree", "unknown"]:
            with patch("provider_models.httpx.get") as get:
                with self.assertRaises(ModelDiscoveryError): list_provider_models({"service": service})
                get.assert_not_called()

    def test_discovery_urls_auth_and_live_ids(self):
        for service, base in MODEL_ENDPOINTS.items():
            with self.subTest(service=service), patch("provider_models.httpx.get", return_value=httpx.Response(200, json={"data": [{"id": "new-model"}]})) as get:
                self.assertEqual(list_provider_models({"service": service, "apiKey": "TEST_KEY", "apiUrl": base}), ["new-model"])
                self.assertEqual(get.call_args.args[0], base + "/models")
                self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer TEST_KEY")
                self.assertEqual(get.call_args.kwargs["params"], {"sub_type": "chat"} if service == "siliconflow" else None)

    def test_compatible_presets_emit_official_chat_contract(self):
        endpoints = {
            "openai": "https://api.openai.com/v1", "deepseek": "https://api.deepseek.com/v1",
            "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
            "aliyundashscope": "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
            "siliconflow": "https://api.siliconflow.cn/v1",
        }
        for service, endpoint in [*endpoints.items(), ("openai", "https://openrouter.ai/api/v1")]:
            with self.subTest(service=service):
                api = {"model": "selected-model", "apiUrl": endpoint}
                config = runtime(service, **api)
                calls = []
                def capture(request):
                    calls.append(request)
                    return httpx.Response(200, json=chat())
                sdk = openai.OpenAI(api_key="TEST_KEY", base_url=endpoint, max_retries=0,
                    http_client=httpx.Client(transport=httpx.MockTransport(capture)))
                self.addCleanup(sdk.close)
                with patch("openai.OpenAI", return_value=sdk) as factory: translator = get_translator(config)
                self.assertEqual(str(factory.call_args.kwargs["base_url"]).rstrip("/"), endpoint)
                self.assertEqual(translator.do_translate("Hello"), "译文")
                request = calls[-1]
                self.assertEqual(str(request.url), endpoint + "/chat/completions")
                self.assertEqual(request.headers["authorization"], "Bearer TEST_KEY")
                body = json.loads(request.content)
                self.assertEqual(body["model"], api["model"])
                self.assertIn("Hello", body["messages"][-1]["content"])

    def test_claude_print_cli_final_result_and_timeout_cleanup(self):
        from pdf2zh_next.translator.translator_impl.claudecode import ClaudeCodeTranslator
        with patch(IMPL + "claudecode.subprocess.run", return_value=NS(returncode=0)):
            translator = ClaudeCodeTranslator(runtime("claudecode", model="sonnet", apiUrl="claude"), NoopRateLimiter())
        process = Mock(returncode=0)
        process.communicate.return_value = (json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "译文"}), "")
        with patch(IMPL + "claudecode.subprocess.Popen", return_value=process) as popen: self.assertEqual(translator.do_translate("Hello"), "译文")
        command = popen.call_args.args[0]
        self.assertEqual(command[command.index("--input-format") + 1], "text")
        self.assertEqual(command[command.index("--output-format") + 1], "json")
        self.assertEqual(command[command.index("--tools") + 1], "")
        self.assertIn("mcp__*", command)
        self.assertIn("Hello", process.communicate.call_args.kwargs["input"])
        for output in ["broken", '{"subtype":"error_max_turns","result":"partial"}', '{"subtype":"success","is_error":true,"result":"bad"}']:
            with self.assertRaises(ValueError): translator._parse_output(output)
        process.communicate.side_effect = [subprocess.TimeoutExpired(command, 120), ("", "")]
        with patch(IMPL + "claudecode.subprocess.Popen", return_value=process):
            with self.assertRaisesRegex(ValueError, "timed out"): translator.do_translate("Hello")
        process.kill.assert_called_once()

    def test_local_sdk_and_deepl_contracts(self):
        from pdf2zh_next.translator.translator_impl.ollama import OllamaTranslator
        from pdf2zh_next.translator.translator_impl.deepl import DeepLTranslator
        with patch(IMPL + "ollama.ollama.Client") as factory:
            translator = OllamaTranslator(runtime("ollama", apiUrl="http://localhost:11434", model="installed-model"), NoopRateLimiter())
            factory.return_value.chat.return_value = NS(message=NS(content="译文"), prompt_eval_count=1, eval_count=2)
            self.assertEqual(translator.do_translate("Hello"), "译文")
            self.assertEqual(factory.call_args.kwargs["host"], "http://localhost:11434")
            self.assertEqual(factory.return_value.chat.call_args.kwargs["model"], "installed-model")
        with patch(IMPL + "deepl.deepl.Translator") as factory:
            translator = DeepLTranslator(runtime("deepl"), NoopRateLimiter())
            factory.return_value.translate_text.return_value = NS(text="译文")
            self.assertEqual(translator.do_translate("Hello"), "译文")
            factory.assert_called_once_with("TEST_KEY")
            factory.return_value.translate_text.assert_called_once_with("Hello", target_lang="ZH-HANS", source_lang="EN")

    def test_no_implicit_service_for_pdf_or_validation(self):
        import server
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from test_server import build_pdf_payload
        with patch("server.validate_service_config") as validate:
            response = server.create_app().test_client().post("/validate-config", json={})
            self.assertEqual(response.status_code, 400)
            validate.assert_not_called()
        for config in [{}, {"service": "siliconflowfree"}]:
            with TemporaryDirectory() as work:
                with self.assertRaises(server.RequestValidationError):
                    server.prepare_translation_request({"fileContent": build_pdf_payload(), "fileName": "test.pdf", **config}, Path(work))
                self.assertEqual(list(Path(work).iterdir()), [])

    def test_engine_catalog_excludes_free_proxy_and_requires_explicit_service(self):
        from pdf2zh_next.config.cli_env_model import CLIEnvSettingsModel
        from pdf2zh_next.config.translate_engine_model import TRANSLATION_ENGINE_METADATA
        engines = {entry.translate_engine_type for entry in TRANSLATION_ENGINE_METADATA}
        self.assertTrue(engines.isdisjoint({"SiliconFlowFree", "Zhipu", "Grok", "Groq", "ModelScope", "QwenMt", "AzureOpenAI", "Azure", "Xinference", "AnythingLLM"}))
        self.assertIn("SiliconFlow", engines)
        settings = CLIEnvSettingsModel().to_settings_model()
        self.assertIsNone(settings.translate_engine_settings)
        with self.assertRaisesRegex(ValueError, "Must provide a translation service"):
            settings.validate_settings()
        settings.basic.warmup = True
        settings.validate_settings()
