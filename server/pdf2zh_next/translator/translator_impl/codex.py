"""Codex translation adapter; the process client is shared, not owned here."""
import threading

from babeldoc.translator.validation import InvalidTranslation
from codex_client import get_codex_client
from pdf2zh_next.translator.base_translator import BaseTranslator


class CodexTranslator(BaseTranslator):
    name = "codex"
    limits_each_attempt = True
    pdf2zh_next_recommended_qps = 2
    pdf2zh_next_recommended_pool_max_workers = 2

    def __init__(self, settings, rate_limiter):
        super().__init__(settings, rate_limiter)
        engine = settings.translate_engine_settings
        self.model = engine.codex_model
        self.reasoning_effort = engine.codex_reasoning_effort
        self.timeout = engine.codex_timeout
        self.client = get_codex_client(engine.codex_cli_path)
        self.check_cancelled = lambda: None
        self._request_slots = threading.BoundedSemaphore(2)
        self.configure_cache_namespace(provider="codex")
        self.add_cache_impact_parameters("model", self.model)
        self.add_cache_impact_parameters("reasoning_effort", self.reasoning_effort)
        self.add_cache_impact_parameters("prompt_template_version", 1)
        self.add_cache_fingerprint("prompt_fingerprint", self.prompt(""))
        self.add_cache_fingerprint("custom_system_prompt_fingerprint", settings.translation.custom_system_prompt)

    def health_check(self):
        """Check login/configuration without spending a translation request."""
        readiness = self.client.check_ready(self.model, self.reasoning_effort,
                                            check_cancelled=self.check_cancelled)
        if readiness.get("reasoningEffort"):
            self.reasoning_effort = readiness["reasoningEffort"]
            self.add_cache_impact_parameters("reasoning_effort", self.reasoning_effort)
        return readiness

    def configure_execution(self, concurrency, check_cancelled, slots=None):
        self._request_slots = slots or threading.BoundedSemaphore(min(2, max(1, concurrency)))
        self.check_cancelled = check_cancelled

    def do_translate(self, text, rate_limit_params=None):
        return self._request(self.prompt(text)[0]["content"], rate_limit_params)

    def do_llm_translate(self, text, rate_limit_params=None):
        if text is None:
            return None
        return self._request(text, rate_limit_params)

    def _request(self, prompt, rate_limit_params=None):
        params = rate_limit_params or {}

        def check():
            self.check_cancelled()
            callback = params.get("check_cancelled")
            if callback is not None and callback is not self.check_cancelled:
                callback()

        collector = self.metrics_collector
        if collector is not None:
            collector.activity_changed("queued", 1)
        try:
            while not self._request_slots.acquire(timeout=0.1):
                check()
        finally:
            if collector is not None:
                collector.activity_changed("queued", -1)
        try:
            check()
            if self.rate_limiter is not None:
                self.rate_limiter.wait({**params, "check_cancelled": check})
            check()
            budget = params.get("request_budget")
            if budget is not None:
                budget.reserve()
            if params.get("on_attempt"):
                params["on_attempt"]()
            kind = params.get("metric_kind", "translation")
            started = collector.request_started(kind=kind) if collector is not None else None
            result, error, text = None, None, None
            try:
                result = self.client.translate(
                    prompt, model=self.model, reasoning_effort=self.reasoning_effort,
                    timeout=float(self.timeout), check_cancelled=check,
                )
                check()
                text = result.text.strip() if isinstance(result.text, str) else ""
                if not text:
                    raise InvalidTranslation("empty_translation")
                return text
            except Exception as exc:
                error = exc
                raise
            finally:
                if collector is not None:
                    usage = getattr(result, "usage", None) or {}
                    collector.record_usage(**{key: usage.get(key) for key in (
                        "prompt_tokens", "completion_tokens", "cache_hit_tokens",
                        "cache_miss_tokens", "reasoning_tokens",
                    )}, kind=kind)
                    collector.request_finished(
                        started, kind=kind, succeeded=error is None,
                        status_code=getattr(error, "status_code", None),
                        error_type=type(error).__name__ if error else None,
                        finish_reason="failed" if error else "completed",
                        protocol="codex_app_server", visible_chars=len(text) if text else None,
                        batch_size=params.get("batch_size"),
                    )
        finally:
            self._request_slots.release()
