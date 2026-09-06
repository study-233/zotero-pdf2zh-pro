import logging
import copy
import math
from urllib.parse import urlparse

import httpx
import openai
from babeldoc.utils.atomic_integer import AtomicInteger
from pdf2zh_next.config.model import SettingsModel
from pdf2zh_next.translator.base_rate_limiter import BaseRateLimiter
from pdf2zh_next.translator.base_translator import BaseTranslator
from tenacity import retry
from tenacity import retry_if_exception
from tenacity import wait_exponential

from pdf2zh_next.translator.openai_protocol import (
    normalize_endpoint,
    parse_request_options,
    wire_options,
    endpoint_unsupported,
    response_text,
)

logger = logging.getLogger(__name__)


def _status_code(error) -> int | None:
    value = getattr(error, "status_code", None)
    if value is None:
        value = getattr(getattr(error, "response", None), "status_code", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _record_retry_before_sleep(retry_state) -> None:
    translator = retry_state.args[0] if retry_state.args else None
    collector = getattr(translator, "metrics_collector", None)
    if collector is not None:
        collector.retry_scheduled()
    error = retry_state.outcome.exception() if retry_state.outcome else None
    logger.warning(
        "provider retry scheduled: error_type=%s status_code=%s attempt=%s",
        type(error).__name__ if error is not None else "unknown",
        _status_code(error),
        retry_state.attempt_number,
    )


def _retry_provider_error(error: Exception) -> bool:
    status = _status_code(error)
    return (
        isinstance(error, openai.APIConnectionError)
        or status in {408, 409, 429}
        or (status is not None and status >= 500)
    )


def _stop_provider_retry(state) -> bool:
    # Keep the existing rate-limit allowance, and replace SDK-hidden transport
    # retries with at most three observable attempts on the same protocol.
    limit = 100 if isinstance(state.outcome.exception(), openai.RateLimitError) else 3
    return state.attempt_number >= limit


class OpenAITranslator(BaseTranslator):
    # https://github.com/openai/openai-python
    name = "openai"

    def __init__(
        self,
        settings: SettingsModel,
        rate_limiter: BaseRateLimiter,
    ):
        super().__init__(settings, rate_limiter)
        self.timeout = settings.translate_engine_settings.openai_timeout
        self.api_protocol = getattr(
            settings.translate_engine_settings,
            "openai_api_protocol",
            "chat_completions",
        )
        base_url, self.protocol_hint = normalize_endpoint(
            settings.translate_engine_settings.openai_base_url, self.api_protocol
        )
        self.resolved_protocol = (
            None if self.api_protocol == "auto" else self.api_protocol
        )
        self.request_options = parse_request_options(
            getattr(settings.translate_engine_settings, "openai_request_options", None)
        )
        self._term_extraction = False
        self.is_deepseek = urlparse(base_url or "").hostname == "api.deepseek.com"
        self.requires_dedicated_term_extraction_translator = self.is_deepseek
        self.client = openai.OpenAI(
            base_url=base_url,
            max_retries=0,
            api_key=settings.translate_engine_settings.openai_api_key,
            timeout=float(self.timeout) if self.timeout else openai.NOT_GIVEN,
            http_client=httpx.Client(
                limits=httpx.Limits(
                    max_connections=None, max_keepalive_connections=None
                )
            ),
        )
        self.options = {}
        self.temperature = settings.translate_engine_settings.openai_temperature
        self.reasoning_effort = (
            settings.translate_engine_settings.openai_reasoning_effort
        )
        self.send_temperature = (
            settings.translate_engine_settings.openai_send_temprature
        )
        self.send_reasoning_effort = (
            settings.translate_engine_settings.openai_send_reasoning_effort
        )

        if self.send_temperature and self.temperature is not None:
            self.add_cache_impact_parameters("temperature", self.temperature)
            self.options["temperature"] = float(self.temperature)
        if self.send_reasoning_effort and self.reasoning_effort:
            self.add_cache_impact_parameters("reasoning_effort", self.reasoning_effort)
            self.options["reasoning_effort"] = self.reasoning_effort

        self.model = settings.translate_engine_settings.openai_model
        self.configure_cache_namespace(provider="openai-compatible")
        self.add_cache_fingerprint(
            "endpoint_fingerprint",
            base_url or str(self.client.base_url),
        )
        self.add_cache_impact_parameters("model", self.model)
        self.add_cache_impact_parameters("prompt_template_version", 2)
        self.add_cache_fingerprint("prompt_fingerprint", self.prompt(""))
        self.add_cache_fingerprint(
            "custom_system_prompt_fingerprint",
            settings.translation.custom_system_prompt,
        )
        self.token_count = AtomicInteger()
        self.prompt_token_count = AtomicInteger()
        self.completion_token_count = AtomicInteger()
        self.cache_hit_prompt_token_count = AtomicInteger()
        self.cache_miss_prompt_token_count = AtomicInteger()

        self.enable_json_mode = (
            settings.translate_engine_settings.openai_enable_json_mode
        )
        if self.enable_json_mode:
            self.add_cache_impact_parameters("enable_json_mode", self.enable_json_mode)

        if self.resolved_protocol:
            self.add_cache_impact_parameters("api_protocol", self.resolved_protocol)
            self.add_cache_fingerprint(
                "request_options", self._options(self.resolved_protocol)
            )

    def configure_for_term_extraction(self) -> None:
        self._term_extraction = True
        if (
            self.is_deepseek
            and (self.resolved_protocol or self.protocol_hint) == "chat_completions"
        ):
            self.add_cache_impact_parameters("deepseek_thinking", "disabled")

    def health_check(self) -> str:
        """Resolve once before cached/parallel paragraph translation begins."""
        first = self.resolved_protocol or self.protocol_hint
        candidates = [first]
        if self.resolved_protocol is None:
            candidates.append(
                "responses" if first == "chat_completions" else "chat_completions"
            )
        for index, protocol in enumerate(candidates):
            try:
                result = self._request(
                    self.prompt("Hello"), protocol, health_check=True
                )
            except Exception as error:
                if index == 0 and len(candidates) == 2 and endpoint_unsupported(error):
                    continue
                raise
            self.resolved_protocol = protocol
            self.requires_dedicated_term_extraction_translator = (
                self.is_deepseek and protocol == "chat_completions"
            )
            self.add_cache_impact_parameters("api_protocol", protocol)
            self.add_cache_fingerprint("request_options", self._options(protocol))
            return result
        raise RuntimeError("No compatible API protocol found")

    def _options(self, protocol: str, rate_limit_params: dict | None = None) -> dict:
        # Normalize each source before merging so native and aliased options
        # supplied by the user override old internal settings consistently.
        defaults = wire_options(self.options, protocol)
        custom = wire_options(self.request_options, protocol)
        for key, value in custom.items():
            if isinstance(value, dict) and isinstance(defaults.get(key), dict):
                defaults[key].update(copy.deepcopy(value))
            else:
                defaults[key] = value
        if (
            self.enable_json_mode
            and rate_limit_params
            and rate_limit_params.get("request_json_mode")
        ):
            if protocol == "responses":
                defaults.setdefault("text", {}).setdefault(
                    "format", {"type": "json_object"}
                )
            else:
                defaults.setdefault("response_format", {"type": "json_object"})
        if (
            self._term_extraction
            and self.is_deepseek
            and protocol == "chat_completions"
        ):
            defaults["thinking"] = {"type": "disabled"}
        return defaults

    def _record_usage(self, response, protocol: str) -> None:
        usage = getattr(response, "usage", None)
        if protocol == "responses":
            prompt = getattr(usage, "input_tokens", None)
            completion = getattr(usage, "output_tokens", None)
            details = getattr(usage, "input_tokens_details", None)
            hit = getattr(details, "cached_tokens", None)
            miss = None
        else:
            prompt = getattr(usage, "prompt_tokens", None)
            completion = getattr(usage, "completion_tokens", None)
            hit = getattr(usage, "prompt_cache_hit_tokens", None)
            if hit is None:
                hit = getattr(
                    getattr(usage, "prompt_tokens_details", None), "cached_tokens", None
                )
            miss = getattr(usage, "prompt_cache_miss_tokens", None)

        def number(value):
            return (
                int(value)
                if isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(value)
                and value >= 0
                else None
            )

        prompt, completion, hit, miss = map(number, (prompt, completion, hit, miss))
        if prompt is not None and hit is not None:
            hit = min(hit, prompt)
            miss = prompt - hit
        self.prompt_token_count.inc(prompt or 0)
        self.completion_token_count.inc(completion or 0)
        self.token_count.inc((prompt or 0) + (completion or 0))
        self.cache_hit_prompt_token_count.inc(hit or 0)
        self.cache_miss_prompt_token_count.inc(miss or 0)
        if self.metrics_collector is not None:
            self.metrics_collector.record_usage(
                prompt_tokens=prompt,
                completion_tokens=completion,
                cache_hit_tokens=hit,
                cache_miss_tokens=miss,
            )

    def _request(
        self,
        messages: list,
        protocol: str,
        rate_limit_params: dict | None = None,
        *,
        health_check: bool = False,
    ) -> str:
        options = self._options(protocol, rate_limit_params)
        client = (
            self.client.with_options(timeout=min(float(self.timeout or 20), 20))
            if health_check
            else self.client
        )
        collector = self.metrics_collector
        started = collector.request_started() if collector else None
        try:
            # extra_body preserves gateway extensions while the adapter owns the
            # model, input and execution mode. Reserved keys were validated above.
            if protocol == "responses":
                response = client.responses.create(
                    model=self.model, input=messages, extra_body=options
                )
            else:
                response = client.chat.completions.create(
                    model=self.model, messages=messages, extra_body=options
                )
            self._record_usage(response, protocol)
            text = self._remove_cot_content(response_text(response, protocol)).strip()
            if not text:
                raise ValueError("API 只返回了推理内容，没有有效译文")
        except Exception as error:
            if collector is not None:
                collector.request_finished(
                    started, succeeded=False, status_code=_status_code(error)
                )
            raise
        if collector is not None:
            collector.request_finished(started, succeeded=True)
        return text

    @retry(
        retry=retry_if_exception(_retry_provider_error),
        stop=_stop_provider_retry,
        wait=wait_exponential(multiplier=1, min=1, max=15),
        before_sleep=_record_retry_before_sleep,
        reraise=True,
    )
    def do_translate(self, text, rate_limit_params: dict = None) -> str:
        return self._request(
            self.prompt(text),
            self.resolved_protocol or self.protocol_hint,
            rate_limit_params,
        )

    @retry(
        retry=retry_if_exception(_retry_provider_error),
        stop=_stop_provider_retry,
        wait=wait_exponential(multiplier=1, min=1, max=15),
        before_sleep=_record_retry_before_sleep,
        reraise=True,
    )
    def do_llm_translate(self, text, rate_limit_params: dict = None):
        if text is None:
            return None
        return self._request(
            [{"role": "user", "content": text}],
            self.resolved_protocol or self.protocol_hint,
            rate_limit_params,
        )
