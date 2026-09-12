import unittest
from types import MethodType

import httpx
from tenacity import wait_none

from observability import TaskMetricsCollector
from pdf2zh_next.translator.translator_impl.openai import OpenAITranslator
import test_openai_protocol as protocol_fixtures

chat, response = protocol_fixtures.chat, protocol_fixtures.response


class MetricsExtensionTests(unittest.TestCase):
    translator = protocol_fixtures.ProtocolTests.translator

    def collector(self):
        return TaskMetricsCollector(task_id="metrics", provider="test", model="test")

    def test_delayed_probe_replay_happens_once_and_does_not_fake_active_requests(self):
        translator = self.translator(lambda _: httpx.Response(200, json=chat("你好")))
        translator.health_check()
        collector = self.collector()
        translator.set_metrics_collector(collector)
        translator.set_metrics_collector(collector)
        metrics = collector.snapshot()
        self.assertEqual(metrics["requests"]["attempts"], 1)
        self.assertEqual(metrics["requests"]["active"], 0)
        self.assertEqual(metrics["requests"]["qps10s"], 0)
        self.assertEqual(metrics["requests"]["byKind"]["initialization"]["statusCodes"], {"200": 1})
        self.assertEqual(metrics["tokens"]["byKind"]["initialization"]["total"], 120)

    def test_reasoning_is_an_output_subset_and_visible_text_excludes_thinking(self):
        for protocol in ("chat_completions", "responses"):
            with self.subTest(protocol=protocol):
                data = chat("<think>private reasoning</think>译文") if protocol == "chat_completions" else response("译文")
                details = "completion_tokens_details" if protocol == "chat_completions" else "output_tokens_details"
                data["usage"][details] = {"reasoning_tokens": 12}
                translator = self.translator(lambda _: httpx.Response(200, json=data), protocol=protocol)
                collector = self.collector()
                translator.set_metrics_collector(collector)
                translator.llm_translate("test prompt", ignore_cache=True,
                                         rate_limit_params={"metric_kind": "review", "batch_size": 4})
                metrics = collector.snapshot()
                self.assertEqual(metrics["tokens"]["total"], 120)
                self.assertEqual(metrics["tokens"]["output"], 20)
                self.assertEqual(metrics["tokens"]["reasoning"], 12)
                self.assertEqual(metrics["tokens"]["reasoningAvailability"], "complete")
                kind = metrics["requests"]["byKind"]["review"]
                self.assertEqual(kind["batchSizes"], {"4": 1})
                self.assertEqual(kind["visibleOutputChars"], 2 if protocol == "chat_completions" else 3)
                self.assertEqual(kind["protocols"], {protocol: 1})
                self.assertEqual(metrics["requests"]["byKind"]["translation"]["attempts"], 0)

    def test_missing_usage_stays_unknown_and_provider_retries_keep_request_kind(self):
        replies = [httpx.Response(429, json={"error": "limited"}),
                   httpx.Response(200, json=chat("译文", usage=None))]
        translator = self.translator(lambda _: replies.pop(0), protocol="chat_completions")
        translator.do_llm_translate = MethodType(OpenAITranslator.do_llm_translate.retry_with(wait=wait_none()), translator)
        collector = self.collector()
        translator.set_metrics_collector(collector)
        translator.llm_translate("test prompt", ignore_cache=True,
                                 rate_limit_params={"metric_kind": "review", "batch_size": 1})
        metrics = collector.snapshot()
        self.assertIsNone(metrics["tokens"]["total"])
        self.assertIsNone(metrics["tokens"]["reasoning"])
        self.assertEqual(metrics["tokens"]["availability"], "unavailable")
        kind = metrics["requests"]["byKind"]["review"]
        self.assertEqual((kind["attempts"], kind["retries"]), (2, 1))
        self.assertEqual(kind["statusCodes"], {"429": 1, "200": 1})
        self.assertEqual(kind["errorTypes"], {"RateLimitError": 1})

    def test_malformed_generation_retry_keeps_review_classification(self):
        from babeldoc.translator.validation import InvalidTranslation
        translator = self.translator(lambda _: httpx.Response(200, json=chat("译文")), protocol="chat_completions")
        collector = self.collector()
        translator.set_metrics_collector(collector)

        def reject(_):
            raise InvalidTranslation("invalid_review")

        with self.assertRaises(InvalidTranslation):
            translator.llm_translate("test prompt", ignore_cache=True,
                rate_limit_params={"metric_kind": "review", "validate_output": reject})
        self.assertEqual(collector.snapshot()["requests"]["byKind"]["review"]["retries"], 1)
        self.assertEqual(collector.snapshot()["requests"]["byKind"]["translation"]["retries"], 0)


if __name__ == "__main__":
    unittest.main()
