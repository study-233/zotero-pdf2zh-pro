from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pdf2zh_next.translator.translator_impl.openai import OpenAITranslator


class NoopRateLimiter:
    def wait(self, _params=None) -> None:
        return None


def settings(
    *,
    model: str = "deepseek-chat",
    lang_in: str = "en",
    lang_out: str = "zh-CN",
    prompt: str | None = None,
    endpoint: str = "https://api.deepseek.com/v1",
    api_key: str = "SECRET_API_KEY",
):
    return SimpleNamespace(
        translation=SimpleNamespace(
            ignore_cache=False,
            lang_in=lang_in,
            lang_out=lang_out,
            custom_system_prompt=prompt,
        ),
        translate_engine_settings=SimpleNamespace(
            openai_timeout=None,
            openai_base_url=endpoint,
            openai_api_key=api_key,
            openai_temperature=None,
            openai_reasoning_effort=None,
            openai_send_temprature=False,
            openai_send_reasoning_effort=False,
            openai_model=model,
            openai_enable_json_mode=False,
        ),
    )


class CacheNamespaceTests(unittest.TestCase):
    def params(self, runtime_settings) -> dict:
        with patch("pdf2zh_next.translator.translator_impl.openai.openai.OpenAI"):
            translator = OpenAITranslator(runtime_settings, NoopRateLimiter())
        translator.configure_cache_namespace(provider="deepseek")
        return json.loads(translator.cache.translate_engine_params)

    def test_namespace_is_safe_stable_and_sensitive_to_translation_inputs(self) -> None:
        params = self.params(settings(prompt="PRIVATE_PROMPT"))
        serialized = json.dumps(params)
        self.assertEqual(params["provider"], "deepseek")
        self.assertEqual(params["model"], "deepseek-chat")
        self.assertEqual(params["lang_in"], "en")
        self.assertEqual(params["lang_out"], "zh-CN")
        self.assertIn("endpoint_fingerprint", params)
        self.assertIn("prompt_fingerprint", params)
        self.assertNotIn("SECRET_API_KEY", serialized)
        self.assertNotIn("PRIVATE_PROMPT", serialized)
        self.assertNotIn("api_key", serialized.lower())
        baseline = self.params(settings())
        for overrides in (
            {"model": "deepseek-reasoner"},
            {"lang_in": "de"},
            {"lang_out": "fr"},
            {"prompt": "Use terse terminology"},
            {"endpoint": "https://example.invalid/v1"},
        ):
            with self.subTest(overrides=overrides):
                self.assertNotEqual(self.params(settings(**overrides)), baseline)
        self.assertEqual(self.params(settings()), baseline)


if __name__ == "__main__":
    unittest.main()
