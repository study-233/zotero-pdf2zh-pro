from __future__ import annotations

import json
import unittest
from unittest.mock import Mock, patch

import httpx
import openai
from tenacity import wait_none

from observability import TaskMetricsCollector
from pdf2zh_next.translator.openai_protocol import (
    normalize_endpoint,
    parse_request_options,
    wire_options,
)
from pdf2zh_next.translator.translator_impl.openai import OpenAITranslator
from test_cache_namespace import settings, NoopRateLimiter


def chat(text="译文", **overrides):
    return {
        "choices": [
            {"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
            "prompt_tokens_details": {"cached_tokens": 70},
        },
        **overrides,
    }


def response(text="译文", **overrides):
    return {
        "id": "resp_test",
        "object": "response",
        "status": "completed",
        "output": [
            {"type": "reasoning", "summary": []},
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": text},
                    {"type": "output_text", "text": "。"},
                ],
            },
        ],
        "usage": {
            "input_tokens": 100,
            "output_tokens": 20,
            "total_tokens": 120,
            "input_tokens_details": {"cached_tokens": 70},
        },
        **overrides,
    }


class ProtocolTests(unittest.TestCase):
    def translator(
        self,
        handler,
        protocol="auto",
        endpoint="https://gateway.invalid/prefix/v1",
        options=None,
    ):
        config = settings(endpoint=endpoint, model="custom-model")
        config.translate_engine_settings.openai_api_protocol = protocol
        config.translate_engine_settings.openai_request_options = json.dumps(
            options or {}
        )
        self.requests = []

        def capture(request):
            self.requests.append((request.url.path, json.loads(request.content)))
            return handler(request)

        base, _ = normalize_endpoint(endpoint, protocol)
        client = openai.OpenAI(
            api_key="SECRET",
            base_url=base,
            max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(capture)),
        )
        self.addCleanup(client.close)
        with patch(
            "pdf2zh_next.translator.translator_impl.openai.openai.OpenAI",
            return_value=client,
        ):
            return OpenAITranslator(config, NoopRateLimiter())

    def test_auto_chat_and_both_supported_use_chat(self):
        translator = self.translator(lambda r: httpx.Response(200, json=chat()))
        self.assertEqual(translator.health_check(), "译文")
        self.assertEqual(translator.resolved_protocol, "chat_completions")
        self.assertEqual(len(self.requests), 1)

    def test_health_check_honors_configured_timeout(self):
        translator = self.translator(lambda r: httpx.Response(200, json=chat()))
        translator.timeout = "120"
        with patch.object(
            translator.client,
            "with_options",
            wraps=translator.client.with_options,
        ) as with_options:
            translator.health_check()
        with_options.assert_called_once_with(timeout=120.0)

    def test_auto_fallback_responses_and_fixed_after_health_check(self):
        def handler(request):
            if request.url.path.endswith("/chat/completions"):
                return httpx.Response(
                    404, json={"error": {"message": "Unknown endpoint"}}
                )
            return httpx.Response(200, json=response())

        translator = self.translator(handler)
        self.assertEqual(translator.health_check(), "译文。")
        self.assertEqual(translator.resolved_protocol, "responses")
        self.assertEqual(translator.do_translate("paper"), "译文。")
        self.assertEqual(translator.do_llm_translate("terms"), "译文。")
        self.assertEqual(
            [p for p, _ in self.requests],
            ["/prefix/v1/chat/completions"] + ["/prefix/v1/responses"] * 3,
        )
        self.assertFalse(self.requests[-1][1]["store"])
        self.assertNotIn("messages", self.requests[-1][1])

    def test_explicit_endpoint_hint_and_reverse_fallback(self):
        for suffix, first, resolved in (
            ("responses", "responses", "chat_completions"),
            ("chat/completions", "chat/completions", "responses"),
        ):
            with self.subTest(suffix=suffix):
                translator = self.translator(
                    lambda r: (
                        httpx.Response(405, json={"error": "Method not allowed"})
                        if r.url.path.endswith(first)
                        else httpx.Response(
                            200,
                            json=chat()
                            if resolved == "chat_completions"
                            else response(),
                        )
                    ),
                    endpoint=f"https://gateway.invalid/custom/{suffix}/",
                )
                translator.health_check()
                self.assertEqual(translator.resolved_protocol, resolved)
                self.assertEqual(len(self.requests), 2)

    def test_auth_model_parameter_rate_timeout_do_not_switch(self):
        for status, message in (
            (401, "Unauthorized"),
            (403, "Forbidden"),
            (429, "Rate limit"),
            (404, "Model not found"),
            (400, "Unsupported parameter"),
            (404, "ambiguous"),
            (500, "Server error"),
        ):
            with self.subTest(status=status, message=message):
                translator = self.translator(
                    lambda r: httpx.Response(
                        status, json={"error": {"message": message}}
                    )
                )
                with self.assertRaises(openai.APIStatusError):
                    translator.health_check()
                self.assertEqual(len(self.requests), 1)

        def timeout(r):
            raise httpx.ReadTimeout("timeout", request=r)

        translator = self.translator(timeout)
        with self.assertRaises(openai.APITimeoutError):
            translator.health_check()
        self.assertEqual(len(self.requests), 1)

    def test_gateway_explicitly_requires_responses_for_model(self):
        translator = self.translator(
            lambda r: (
                httpx.Response(
                    400,
                    json={
                        "error": {
                            "message": "This model only supports the Responses API"
                        }
                    },
                )
                if r.url.path.endswith("chat/completions")
                else httpx.Response(200, json=response())
            )
        )
        translator.health_check()
        self.assertEqual(translator.resolved_protocol, "responses")
        self.assertEqual(len(self.requests), 2)

    def test_localized_protocol_rejection_switches_once_and_preserves_denial(self):
        for rejection in (
            {"code": "protocol_not_supported", "message": "模型 custom-model 不支持 chat completions 协议"},
            {"message": "模型 custom-model 不支持 chat completions 协议"},
            {"code": "unsupported_protocol", "message": "请使用其他协议"},
            {"message": "该模型仅支持Responses协议"},
        ):
            with self.subTest(rejection=rejection):
                translator = self.translator(lambda r: (
                    httpx.Response(400, json={"error": rejection})
                    if r.url.path.endswith("chat/completions")
                    else httpx.Response(200, json=response())
                ))
                translator.health_check()
                self.assertEqual(translator.resolved_protocol, "responses")
                self.assertEqual(len(self.requests), 2)
        translator = self.translator(lambda r: (
            httpx.Response(400, json={"error": {"code": "protocol_not_supported"}})
            if r.url.path.endswith("chat/completions")
            else httpx.Response(403, json={"error": {"message": "请使用标准 Codex 客户端"}})
        ))
        with self.assertRaises(openai.PermissionDeniedError):
            translator.health_check()
        self.assertEqual(len(self.requests), 2)
        self.assertIsNone(translator.resolved_protocol)

    def test_protocol_code_cannot_override_auth_model_or_parameter_errors(self):
        for status, error in (
            (403, {"code": "protocol_not_supported"}),
            (429, {"code": "protocol_not_supported"}),
            (404, {"code": "protocol_not_supported", "message": "模型不存在"}),
            (404, {"code": "protocol_not_supported", "message": "Model custom-model not found"}),
            (400, {"code": "unsupported_model", "message": "Unsupported model for responses"}),
            (400, {"code": "protocol_not_supported", "message": "参数错误"}),
            (400, {"message": "This model is not supported; responses route is available"}),
        ):
            with self.subTest(status=status, error=error):
                translator = self.translator(lambda r: httpx.Response(status, json={"error": error}))
                with self.assertRaises(openai.APIStatusError):
                    translator.health_check()
                self.assertEqual(len(self.requests), 1)
        translator = self.translator(
            lambda r: httpx.Response(400, json={"error": {"code": "protocol_not_supported"}}),
            protocol="chat_completions",
        )
        with self.assertRaises(openai.BadRequestError):
            translator.health_check()
        self.assertEqual(len(self.requests), 1)

    def test_manual_and_paragraph_failure_never_probe_other_protocol(self):
        translator = self.translator(
            lambda r: httpx.Response(404, json={"error": "Not found"}),
            protocol="responses",
        )
        with self.assertRaises(openai.NotFoundError):
            translator.health_check()
        self.assertEqual(len(self.requests), 1)
        state = [True]
        translator = self.translator(
            lambda r: (
                httpx.Response(200, json=chat())
                if state[0]
                else httpx.Response(404, json={"error": "Not found"})
            )
        )
        translator.health_check()
        state[0] = False
        with self.assertRaises(openai.NotFoundError):
            OpenAITranslator.do_translate.retry_with(wait=wait_none())(translator, "paper")
        self.assertTrue(
            all(path.endswith("chat/completions") for path, _ in self.requests)
        )

    def test_request_mapping_extensions_and_prompt_preserved(self):
        translator = self.translator(
            lambda r: httpx.Response(200, json=response()),
            protocol="responses",
            options={
                "max_tokens": 123,
                "reasoning_effort": "low",
                "temperature": 0,
                "vendor_option": {"enabled": False},
            },
        )
        translator.enable_json_mode = True
        translator.do_translate("paper", {"request_json_mode": True})
        _, request = self.requests[0]
        self.assertEqual(request["input"], translator.prompt("paper"))
        self.assertEqual(request["max_output_tokens"], 123)
        self.assertEqual(request["reasoning"], {"effort": "low"})
        self.assertEqual(request["text"]["format"], {"type": "json_object"})
        self.assertEqual(request["temperature"], 0)
        self.assertEqual(request["vendor_option"], {"enabled": False})
        self.assertNotIn("max_tokens", request)

    def test_bad_responses_not_cached(self):
        cases = [
            ("chat_completions", chat(choices=[])),
            ("chat_completions", chat(text=None)),
            (
                "chat_completions",
                chat(
                    choices=[
                        {"message": {"content": "partial"}, "finish_reason": "length"}
                    ]
                ),
            ),
            (
                "chat_completions",
                chat(choices=[{"message": {"refusal": "no"}, "finish_reason": "stop"}]),
            ),
            ("responses", response(status="incomplete")),
            ("responses", response(status="failed")),
            ("responses", response(output=[])),
            ("responses", response(text=None)),
            (
                "responses",
                response(
                    output=[
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "refusal", "refusal": "no"}],
                        }
                    ]
                ),
            ),
        ]
        for protocol, body in cases:
            with self.subTest(protocol=protocol, body=body):
                translator = self.translator(
                    lambda r: httpx.Response(200, json=body), protocol=protocol
                )
                translator.cache = Mock()
                translator.cache.get.return_value = None
                with self.assertRaises(ValueError):
                    translator.translate("source", ignore_cache=True)
                translator.cache.set.assert_not_called()

    def test_usage_two_protocols_missing_and_partial(self):
        for protocol, body in (("chat_completions", chat()), ("responses", response())):
            with self.subTest(protocol=protocol):
                payload = [body]
                translator = self.translator(
                    lambda r: httpx.Response(200, json=payload[0]), protocol=protocol
                )
                collector = TaskMetricsCollector(
                    task_id="test", provider="gateway", model="model"
                )
                translator.set_metrics_collector(collector)
                translator.do_translate("paper")
                metrics = collector.snapshot()
                self.assertEqual(metrics["tokens"]["total"], 120)
                self.assertEqual(metrics["providerCache"]["hitTokens"], 70)
                self.assertEqual(metrics["providerCache"]["missTokens"], 30)
                self.assertNotIn("cost", metrics)
                payload[0] = {**body, "usage": None}
                translator.do_translate("another")
                metrics = collector.snapshot()
                self.assertEqual(metrics["tokens"]["total"], 120)
                self.assertEqual(metrics["tokens"]["availability"], "partial")
                self.assertEqual(metrics["providerCache"]["availability"], "partial")
                self.assertEqual(metrics["requests"]["succeeded"], 2)
        collector = TaskMetricsCollector(
            task_id="test", provider="gateway", model="model"
        )
        collector.record_usage(
            prompt_tokens=None,
            completion_tokens=None,
            cache_hit_tokens=None,
            cache_miss_tokens=None,
        )
        self.assertIsNone(collector.snapshot()["tokens"]["total"])

    def test_options_and_url_validation(self):
        self.assertEqual(
            normalize_endpoint("https://host/prefix/responses/", "auto"),
            ("https://host/prefix", "responses"),
        )
        self.assertEqual(
            normalize_endpoint("https://host", "responses")[0], "https://host"
        )
        with self.assertRaises(ValueError):
            normalize_endpoint("https://host/responses", "chat_completions")
        for options in (
            {"stream": True},
            {"model": "override"},
            {"extra_body": {"input": "bad"}},
            [],
            "invalid",
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                parse_request_options(options)
        with self.assertRaises(ValueError):
            wire_options({"max_tokens": 1, "max_output_tokens": 2}, "responses")
        with self.assertRaises(ValueError):
            wire_options(
                {"reasoning_effort": "low", "reasoning": {"effort": "high"}},
                "responses",
            )
        self.assertEqual(
            wire_options({"max_output_tokens": 10}, "chat_completions"),
            {"max_completion_tokens": 10},
        )

    def test_protocol_and_options_isolate_cache(self):
        fingerprints = []
        for protocol, options in (
            ("chat_completions", {}),
            ("responses", {}),
            ("responses", {"temperature": 0}),
        ):
            translator = self.translator(
                lambda r: httpx.Response(
                    200, json=chat() if protocol == "chat_completions" else response()
                ),
                protocol=protocol,
                options=options,
            )
            translator.health_check()
            fingerprints.append(translator.cache.translate_engine_params)
        self.assertEqual(len(set(fingerprints)), 3)
        self.assertTrue(all("SECRET" not in value for value in fingerprints))

    def test_transient_retries_are_counted_without_protocol_switch(self):
        for status in (429, 503):
            with self.subTest(status=status):
                calls = []

                def handler(request):
                    calls.append(request)
                    return (
                        httpx.Response(status, json={"error": "temporary"})
                        if len(calls) == 1
                        else httpx.Response(200, json=response())
                    )

                translator = self.translator(handler, protocol="responses")
                collector = TaskMetricsCollector(
                    task_id="retry", provider="gateway", model="model"
                )
                translator.set_metrics_collector(collector)
                translate = OpenAITranslator.do_translate.retry_with(wait=wait_none())
                self.assertEqual(translate(translator, "source"), "译文。")
                metrics = collector.snapshot()["requests"]
                self.assertEqual(
                    (
                        metrics["attempts"],
                        metrics["failed"],
                        metrics["succeeded"],
                        metrics["retries"],
                    ),
                    (2, 1, 1, 1),
                )
                self.assertTrue(
                    all(path.endswith("/responses") for path, _ in self.requests)
                )

    def test_native_user_options_override_internal_aliases(self):
        translator = self.translator(
            lambda r: httpx.Response(200, json=response()),
            protocol="responses",
            options={"reasoning": {"effort": "low"}, "temperature": 0},
        )
        translator.options = {"reasoning_effort": "high", "temperature": 1}
        translator.do_translate("source")
        request = self.requests[-1][1]
        self.assertEqual(request["reasoning"], {"effort": "low"})
        self.assertEqual(request["temperature"], 0)


if __name__ == "__main__":
    unittest.main()
