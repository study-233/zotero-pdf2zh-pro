"""Offline provider contracts from docs; no real credentials or model calls."""
import json
import subprocess
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import httpx
import openai
from azure.ai.translation.text import TextTranslationClient
from azure.core.credentials import AzureKeyCredential
from azure.core.pipeline.transport import HttpTransport, HttpResponse
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


class AzureResponse(HttpResponse):
    def __init__(self, request):
        super().__init__(request, None)
        self.status_code = 200
        self.headers = {"content-type": "application/json"}
        self.content_type = "application/json"
    def body(self):
        return json.dumps({"value": [{"translations": [{"text": "译文", "language": "zh-Hans"}]}]}).encode()

    def json(self):
        return json.loads(self.body())


class AzureTransport(HttpTransport):
    def open(self): pass
    def close(self): pass
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def send(self, request, **_):
        self.request = request
        return AzureResponse(request)


class ProviderContractTests(unittest.TestCase):
    def test_removed_and_unknown_services_never_select_default_engine(self):
        import server
        client = server.create_app().test_client()
        self.assertEqual(len(SERVICE_FIELD_MAP), 19)
        for service in ["OpenAI_Compatible", "tencentmechinetranslation", "Dify", "unknown", "", None, 1]:
            with self.subTest(service=service), patch("server.validate_service_config") as validate:
                with self.assertRaises(ValueError): require_supported_service(service)
                self.assertEqual(client.post("/validate-config", json={"service": service}).status_code, 400)
                validate.assert_not_called()
                with self.assertRaises(ValueError): runtime(service)
        for service in ["openaicompatible", "tencentmechinetranslation", "dify", "unknown"]:
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
            "gemini": "https://generativelanguage.googleapis.com/v1beta/openai", "grok": "https://api.x.ai/v1",
            "groq": "https://api.groq.com/openai/v1", "zhipu": "https://open.bigmodel.cn/api/paas/v4",
            "modelscope": "https://api-inference.modelscope.cn/v1",
            "aliyundashscope": "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
            "siliconflow": "https://api.siliconflow.cn/v1",
            "qwenmt": "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
            "azureopenai": "https://resource.openai.azure.com/openai/v1",
        }
        for service, endpoint in endpoints.items():
            with self.subTest(service=service):
                api = {"model": "qwen-mt-plus" if service == "qwenmt" else "selected-model"}
                if service in ["openai", "aliyundashscope", "qwenmt", "azureopenai"]: api["apiUrl"] = endpoint
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
                if service == "azureopenai": self.assertNotIn("temperature", body)
                if service == "qwenmt":
                    self.assertEqual(body["messages"], [{"role": "user", "content": "Hello"}])
                    self.assertEqual(body["translation_options"]["target_lang"], "Chinese")

    def test_azure_openai_root_url_and_explicit_legacy_version(self):
        from pdf2zh_next.translator.translator_impl.azureopenai import AzureOpenAITranslator
        with patch("openai.OpenAI") as factory:
            AzureOpenAITranslator(runtime("azureopenai", apiUrl="https://resource.openai.azure.com/", model="my-deployment"), NoopRateLimiter())
            self.assertEqual(factory.call_args.kwargs["base_url"], "https://resource.openai.azure.com/openai/v1")
        with patch("openai.AzureOpenAI") as factory:
            AzureOpenAITranslator(runtime("azureopenai", apiUrl="https://resource.openai.azure.com", model="my-deployment", extraData={"azure_openai_api_version": "2024-10-21"}), NoopRateLimiter())
            self.assertEqual(factory.call_args.kwargs["api_version"], "2024-10-21")
            self.assertEqual(factory.call_args.kwargs["azure_deployment"], "my-deployment")

    def test_azure_sdk2_serializes_configured_region_and_languages(self):
        from pdf2zh_next.translator.translator_impl.azure import AzureTranslator
        for region in ["eastus", ""]:
            transport = AzureTransport()
            sdk = TextTranslationClient(credential=AzureKeyCredential("TEST_KEY"), region=region or None, transport=transport)
            with patch(IMPL + "azure.TextTranslationClient", return_value=sdk) as factory:
                translator = AzureTranslator(runtime("azure", apiUrl="https://api.cognitive.microsofttranslator.com", azureRegion=region), NoopRateLimiter())
            self.assertEqual(factory.call_args.kwargs["region"], region or None)
            self.assertEqual(translator.do_translate("Hello"), "译文")
            request = transport.request
            self.assertIn("api-version=2026-06-06", request.url)
            self.assertEqual(request.headers["Ocp-Apim-Subscription-Key"], "TEST_KEY")
            self.assertEqual(request.headers.get("Ocp-Apim-Subscription-Region"), region or None)
            body = json.loads(request.body)
            self.assertEqual(body["inputs"][0]["text"], "Hello")
            self.assertEqual(body["inputs"][0]["targets"][0]["language"], "zh-Hans")

    def test_azure_china_preserves_v3_and_legacy_region(self):
        from pdf2zh_next.translator.translator_impl.azure import AzureTranslator
        requests = []
        def capture(request):
            requests.append(request)
            return httpx.Response(200, json=[{"translations": [{"text": "译文"}]}])
        client = httpx.Client(transport=httpx.MockTransport(capture))
        self.addCleanup(client.close)
        with patch(IMPL + "azure.httpx.Client", return_value=client): translator = AzureTranslator(runtime("azure"), NoopRateLimiter())
        self.assertEqual(translator.do_translate("Hello"), "译文")
        request = requests[0]
        self.assertEqual(request.url.host, "api.translator.azure.cn")
        self.assertEqual(request.url.params["api-version"], "3.0")
        self.assertEqual(request.headers["Ocp-Apim-Subscription-Region"], "chinaeast2")
        self.assertEqual(json.loads(request.content), [{"Text": "Hello"}])

    def test_anythingllm_string_and_separate_sessions(self):
        from pdf2zh_next.translator.translator_impl.anythingllm import AnythingLLMTranslator
        endpoint = "http://localhost:3001/api/v1/workspace/papers/chat"
        translator = AnythingLLMTranslator(runtime("anythingllm", apiUrl=endpoint), NoopRateLimiter())
        response = Mock()
        response.json.return_value = {"type": "textResponse", "textResponse": "译文", "error": None}
        with patch(IMPL + "anythingllm.requests.post", return_value=response) as post:
            self.assertEqual(translator.do_translate("Hello"), "译文")
            translator.do_translate("Hello again")
        payloads = [json.loads(call.kwargs["data"]) for call in post.call_args_list]
        self.assertIsInstance(payloads[0]["message"], str)
        self.assertIn("Hello", payloads[0]["message"])
        self.assertNotEqual(payloads[0]["sessionId"], payloads[1]["sessionId"])
        self.assertEqual(post.call_args.args[0], endpoint)
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer TEST_KEY")
        response.json.return_value = {"textResponse": None, "error": "abort"}
        with patch(IMPL + "anythingllm.requests.post", return_value=response):
            with self.assertRaises(ValueError): translator.do_translate.__wrapped__(translator, "Hello")

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
        from pdf2zh_next.translator.translator_impl.xinference import XinferenceTranslator
        from pdf2zh_next.translator.translator_impl.deepl import DeepLTranslator
        with patch(IMPL + "ollama.ollama.Client") as factory:
            translator = OllamaTranslator(runtime("ollama", apiUrl="http://localhost:11434", model="installed-model"), NoopRateLimiter())
            factory.return_value.chat.return_value = NS(message=NS(content="译文"), prompt_eval_count=1, eval_count=2)
            self.assertEqual(translator.do_translate("Hello"), "译文")
            self.assertEqual(factory.call_args.kwargs["host"], "http://localhost:11434")
            self.assertEqual(factory.return_value.chat.call_args.kwargs["model"], "installed-model")
        with patch(IMPL + "xinference.Client") as factory:
            translator = XinferenceTranslator(runtime("xinference", apiUrl="http://localhost:9997", model="deployed-uid"), NoopRateLimiter())
            factory.return_value.get_model.return_value.chat.return_value = chat()
            self.assertEqual(translator.do_translate("Hello"), "译文")
            factory.return_value.get_model.assert_called_once_with("deployed-uid")
            self.assertEqual(factory.call_args.kwargs["base_url"], "http://localhost:9997")
        with patch(IMPL + "deepl.deepl.Translator") as factory:
            translator = DeepLTranslator(runtime("deepl"), NoopRateLimiter())
            factory.return_value.translate_text.return_value = NS(text="译文")
            self.assertEqual(translator.do_translate("Hello"), "译文")
            factory.assert_called_once_with("TEST_KEY")
            factory.return_value.translate_text.assert_called_once_with("Hello", target_lang="ZH-HANS", source_lang="EN")

    def test_free_proxy_is_distinct_from_siliconflow_api(self):
        from pdf2zh_next.translator.translator_impl.siliconflowfree import SiliconFlowFreeTranslator
        with patch.object(SiliconFlowFreeTranslator, "get_fast_service"), patch.object(SiliconFlowFreeTranslator, "fetch_setting"), patch(IMPL + "siliconflowfree.httpx.Client") as factory:
            translator = SiliconFlowFreeTranslator(runtime("siliconflowfree"), NoopRateLimiter())
            factory.return_value.post.return_value.json.return_value = {"content": "译文"}
            self.assertEqual(translator.do_translate("Hello"), "译文")
            call = factory.return_value.post.call_args
            self.assertEqual(call.args[0], "https://api1.pdf2zh-next.com/chatproxy")
            self.assertIn("Hello", call.kwargs["json"]["text"])
            self.assertNotIn("headers", call.kwargs)
