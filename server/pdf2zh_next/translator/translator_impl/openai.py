import logging
import copy
import math
import threading
import time
import random
from email.utils import parsedate_to_datetime
from babeldoc.translator.validation import InvalidTranslation
from urllib.parse import urlparse

import httpx
import openai
from babeldoc.utils.atomic_integer import AtomicInteger
from pdf2zh_next.config.model import SettingsModel
from pdf2zh_next.translator.base_rate_limiter import BaseRateLimiter
from pdf2zh_next.translator.base_translator import BaseTranslator
from tenacity import retry
from tenacity import retry_if_exception

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
        params = retry_state.kwargs.get("rate_limit_params") or (
            retry_state.args[2] if len(retry_state.args) > 2 else {}) or {}
        collector.retry_scheduled(kind=params.get("metric_kind", "translation"))
    error = retry_state.outcome.exception() if retry_state.outcome else None
    logger.warning(
        "provider retry scheduled: error_type=%s status_code=%s attempt=%s",
        type(error).__name__ if error is not None else "unknown",
        _status_code(error),
        retry_state.attempt_number,
    )
    if translator is not None:
        translator._wait_cancel(retry_state.next_action.sleep)


def _retry_provider_error(error: Exception) -> bool:
    status = _status_code(error)
    return (
        isinstance(error, openai.APIConnectionError)
        or bool(getattr(error, "retryable_verified_404", False))
        or status in {408, 409, 429}
        or (status is not None and status >= 500)
    )


def _stop_provider_retry(state) -> bool:
    return state.attempt_number >= 5


def _retry_wait(state):
    delay = min(30, 2 ** state.attempt_number) + random.uniform(0, 1)
    error = state.outcome.exception()
    response = getattr(error, "response", None)
    value = response.headers.get("retry-after") if response is not None else None
    if value:
        try:
            seconds = float(value)
        except ValueError:
            try:
                seconds = parsedate_to_datetime(value).timestamp() - time.time()
            except (ValueError, TypeError, OverflowError):
                seconds = 0
        if math.isfinite(seconds):
            delay = max(delay, seconds)
    return delay



class OpenAITranslator(BaseTranslator):
    # https://github.com/openai/openai-python
    name = "openai"
    limits_each_attempt = True

    def __init__(
        self,
        settings: SettingsModel,
        rate_limiter: BaseRateLimiter,
    ):
        super().__init__(settings, rate_limiter)
        self._metrics_lock = threading.RLock()
        self._pending_initialization_metrics = []
        self._request_metrics = threading.local()
        self._verified_protocols = set()
        self._request_slots = threading.BoundedSemaphore(1)
        self.check_cancelled = lambda: None
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
        # A read-only hook captures actual HTTP status while preserving the SDK's
        # create/parse/retry behavior (including with_options during health checks).
        http_client = getattr(self.client, "_client", None)
        if isinstance(http_client, httpx.Client):
            http_client.event_hooks.setdefault("response", []).append(self._capture_http_status)
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

    def _capture_http_status(self, response):
        self._request_metrics.status_code = response.status_code

    def set_metrics_collector(self, collector) -> None:
        with self._metrics_lock:
            self.metrics_collector = collector
            if collector is None:
                return
            pending, self._pending_initialization_metrics = self._pending_initialization_metrics, []
            for event in pending:
                collector.record_usage(**event["usage"], kind="initialization")
                collector.record_completed_request(**event["request"])

    def _record_usage(self, response, protocol: str) -> None:
        usage = getattr(response, "usage", None)
        if protocol == "responses":
            prompt = getattr(usage, "input_tokens", None)
            completion = getattr(usage, "output_tokens", None)
            details = getattr(usage, "input_tokens_details", None)
            hit = getattr(details, "cached_tokens", None)
            miss = None
            reasoning = getattr(getattr(usage, "output_tokens_details", None), "reasoning_tokens", None)
        else:
            prompt = getattr(usage, "prompt_tokens", None)
            completion = getattr(usage, "completion_tokens", None)
            hit = getattr(usage, "prompt_cache_hit_tokens", None)
            if hit is None:
                hit = getattr(
                    getattr(usage, "prompt_tokens_details", None), "cached_tokens", None
                )
            miss = getattr(usage, "prompt_cache_miss_tokens", None)
            reasoning = getattr(getattr(usage, "completion_tokens_details", None), "reasoning_tokens", None)
        if reasoning is None:
            reasoning = getattr(usage, "reasoning_tokens", None)

        def number(value):
            return (
                int(value)
                if isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(value)
                and value >= 0
                else None
            )

        prompt, completion, hit, miss, reasoning = map(number, (prompt, completion, hit, miss, reasoning))
        if prompt is not None and hit is not None:
            hit = min(hit, prompt)
            miss = prompt - hit
        self.prompt_token_count.inc(prompt or 0)
        self.completion_token_count.inc(completion or 0)
        self.token_count.inc((prompt or 0) + (completion or 0))
        self.cache_hit_prompt_token_count.inc(hit or 0)
        self.cache_miss_prompt_token_count.inc(miss or 0)
        values = dict(prompt_tokens=prompt, completion_tokens=completion,
                      cache_hit_tokens=hit, cache_miss_tokens=miss, reasoning_tokens=reasoning)
        self._request_metrics.usage = values
        if self.metrics_collector is not None and not getattr(self._request_metrics, "capturing", False):
            self.metrics_collector.record_usage(**values)

    def _response_metrics(self, response, protocol):
        finish, texts = None, []
        if protocol == "responses":
            finish = getattr(getattr(response, "incomplete_details", None), "reason", None) or getattr(response, "status", None)
            for item in getattr(response, "output", None) or []:
                if getattr(item, "type", None) == "message":
                    texts.extend(getattr(block, "text", None) for block in getattr(item, "content", None) or []
                                 if getattr(block, "type", None) == "output_text")
        else:
            choices = getattr(response, "choices", None) or []
            if choices:
                finish = getattr(choices[0], "finish_reason", None)
                texts = [getattr(getattr(choices[0], "message", None), "content", None)]
        allowed = {"stop", "length", "content_filter", "tool_calls", "function_call", "completed",
                   "incomplete", "failed", "cancelled", "queued", "in_progress", "max_output_tokens"}
        finish = finish if isinstance(finish, str) and finish in allowed else "other" if finish is not None else None
        visible = [text for text in texts if isinstance(text, str)]
        chars = len(self._remove_cot_content("".join(visible)).strip()) if visible else None
        return finish, chars

    def configure_execution(self, concurrency, check_cancelled, slots=None):
        self._request_slots = slots or threading.BoundedSemaphore(max(1, concurrency))
        self.check_cancelled = check_cancelled

    def _wait_cancel(self, seconds):
        deadline = time.monotonic() + seconds
        while True:
            self.check_cancelled()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.1, remaining))

    def _request(self, messages, protocol, rate_limit_params=None, *, health_check=False):
        while not self._request_slots.acquire(timeout=0.1):
            self.check_cancelled()
        try:
            self.check_cancelled()
            self.rate_limiter.wait({**(rate_limit_params or {}), "check_cancelled": self.check_cancelled})
            self.check_cancelled()
            callback = (rate_limit_params or {}).get("on_attempt")
            if callback:
                callback()
            return self._request_unlimited(messages, protocol, rate_limit_params, health_check=health_check)
        finally:
            self._request_slots.release()

    def _request_unlimited(
        self,
        messages: list,
        protocol: str,
        rate_limit_params: dict | None = None,
        *,
        health_check: bool = False,
    ) -> str:
        options = self._options(protocol, rate_limit_params)
        client = (
            self.client.with_options(timeout=float(self.timeout or 20))
            if health_check
            else self.client
        )
        kind = "initialization" if health_check else (rate_limit_params or {}).get("metric_kind", "translation")
        if kind not in {"translation", "review", "initialization"}:
            kind = "translation"
        batch_size = (rate_limit_params or {}).get("batch_size")
        if type(batch_size) is not int or batch_size < 1:
            batch_size = None
        collector = self.metrics_collector
        started = collector.request_started(kind=kind) if collector else None
        request_started = time.perf_counter()
        self._request_metrics.status_code = None
        self._request_metrics.usage = None
        self._request_metrics.capturing = True
        response, request_error = None, None
        succeeded = False
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
            try:
                text = self._remove_cot_content(response_text(response, protocol)).strip()
            except ValueError as error:
                raise InvalidTranslation("invalid_response") from error
            if not text:
                raise InvalidTranslation("empty_translation")
            succeeded = True
        except Exception as error:
            request_error = error
            if _status_code(error) == 404 and protocol in self._verified_protocols:
                body = str(getattr(error, "body", "")).lower()
                permanent = any(x in body for x in (
                    "model_not_found", "model not found", "model does not exist",
                    "unknown model", "unknown endpoint", "route not found", "endpoint not found",
                    "模型不存在", "模型未找到", "无此模型", "路由不存在",
                ))
                error.retryable_verified_404 = not permanent
            raise
        finally:
            if self._request_metrics.usage is None:
                self._record_usage(response, protocol)
            self._request_metrics.capturing = False
            finish, visible_chars = self._response_metrics(response, protocol)
            status = self._request_metrics.status_code
            if status is None and request_error is not None:
                status = _status_code(request_error)
            request = dict(kind=kind, succeeded=succeeded, status_code=status,
                           error_type=type(request_error).__name__ if request_error else None,
                           finish_reason=finish, protocol=protocol,
                           visible_chars=visible_chars, batch_size=batch_size)
            usage = self._request_metrics.usage
            if collector is not None:
                collector.record_usage(**usage, kind=kind)
                collector.request_finished(started, **request)
            elif kind == "initialization":
                request["latency_ms"] = max((time.perf_counter() - request_started) * 1000, 0.0)
                with self._metrics_lock:
                    current_collector = self.metrics_collector
                    if current_collector is None:
                        self._pending_initialization_metrics.append({"request": request, "usage": usage})
                    else:
                        current_collector.record_usage(**usage, kind=kind)
                        current_collector.record_completed_request(**request)
        self._verified_protocols.add(protocol)
        return text

    @retry(
        retry=retry_if_exception(_retry_provider_error),
        stop=_stop_provider_retry,
        wait=_retry_wait,
        sleep=lambda _: None,
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
        wait=_retry_wait,
        sleep=lambda _: None,
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
