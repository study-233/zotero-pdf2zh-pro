"""Shared request admission, cancellation, cache and metrics for account providers."""
import asyncio
import threading
from babeldoc.translator.validation import InvalidTranslation
from pdf2zh_next.translator.base_translator import BaseTranslator


class AccountTranslator(BaseTranslator):
    limits_each_attempt = True
    pdf2zh_next_recommended_qps = 2
    pdf2zh_next_recommended_pool_max_workers = 2
    default_protocol = 'responses'

    def health_check(self):
        """Check login/configuration without spending a translation request."""
        readiness = self.client.check_ready(self.model, self.reasoning_effort,
                                            check_cancelled=self.check_cancelled)
        if readiness.get("reasoningEffort"):
            self.reasoning_effort = readiness["reasoningEffort"]
            self.add_cache_impact_parameters("reasoning_effort", self.reasoning_effort)
        return readiness

    def configure_execution(self, concurrency, check_cancelled, slots=None):
        self._request_slots = slots or threading.BoundedSemaphore(
            min(self.pdf2zh_next_recommended_pool_max_workers, max(1, concurrency)))
        self.check_cancelled = check_cancelled

    def do_translate(self, text, rate_limit_params=None):
        return self._request(self.prompt(text)[0]["content"], rate_limit_params)

    def do_llm_translate(self, text, rate_limit_params=None):
        if text is None:
            return None
        return self._request(text, rate_limit_params)

    def selection_stream(self, prompt, on_text, cancellation):
        return self._request(prompt, on_text=on_text)

    def _request(self, prompt, rate_limit_params=None, *, on_text=None):
        params = rate_limit_params or {}

        def check():
            self.check_cancelled()
            callback = params.get("check_cancelled")
            if callback is not None and callback is not self.check_cancelled:
                callback()

        def check_pending():
            check()
            self.request_state.check()

        check_pending()
        collector = self.metrics_collector
        if collector is not None:
            collector.activity_changed("queued", 1)
        try:
            while not self._request_slots.acquire(timeout=0.1):
                check_pending()
        finally:
            if collector is not None:
                collector.activity_changed("queued", -1)
        try:
            check_pending()
            if self.rate_limiter is not None:
                self.rate_limiter.wait({**params, "check_cancelled": check_pending})
            check_pending()
            budget = params.get("request_budget")
            if budget is not None:
                budget.reserve()
            if params.get("on_attempt"):
                params["on_attempt"]()
            kind = params.get("metric_kind", "translation")
            started = None
            auth_retry = False
            def begin_request():
                nonlocal started
                if auth_retry:
                    check_pending()
                    if self.rate_limiter is not None:
                        self.rate_limiter.wait({**params, "check_cancelled": check_pending})
                    check_pending()
                    if budget is not None:
                        budget.reserve()
                    if params.get("on_attempt"):
                        params["on_attempt"]()
                if collector is not None:
                    started = collector.request_started(kind=kind, operation_id=params.get('metric_operation_id'),
                        paragraph_refs=params.get('diagnostic_paragraph_refs'),
                        paragraph_ids=params.get('paragraph_ids'), parent_call=params.get('_metric_last_call'),
                        retry_cause='auth_refresh' if auth_retry else params.get('metric_retry_cause'))
                    if auth_retry:
                        collector.retry_scheduled(kind=kind)
                    params['_metric_last_call'] = started
                    return started.call_id
            def finish_rejected_request():
                nonlocal started, auth_retry
                if collector is not None and started is not None:
                    collector.request_finished(started, kind=kind, succeeded=False, status_code=401,
                        error_type='ChatGPTError', finish_reason='failed', protocol='responses',
                        batch_size=params.get('batch_size'))
                started = None
                auth_retry = True
            if self.name != 'chatgpt':
                begin_request()
            error, text, usage = None, None, {}
            completed = succeeded = False
            try:
                result = self.client.translate(
                    prompt, model=self.model, reasoning_effort=self.reasoning_effort,
                    timeout=float(self.timeout), check_cancelled=check,
                    check_admission=check_pending,
                    on_usage=usage.update, **({"on_text": on_text} if on_text else {}),
                    **({'purpose': params.get('metric_purpose', kind), 'on_request_start': begin_request,
                        'on_auth_retry': finish_rejected_request}
                       if self.name == 'chatgpt' else {}),
                    **({'call_id': started.call_id} if started is not None
                       and getattr(self.client, 'accepts_call_id', False) is True else {}),
                )
                completed = True
                usage.update(result.usage or {})
                check()
                text = result.text.strip() if isinstance(result.text, str) else ""
                if not text:
                    raise InvalidTranslation("empty_translation")
                succeeded = True
                return text
            except BaseException as exc:
                error = exc
                self.request_state.stop_for(exc)
                raise
            finally:
                if collector is not None and started is not None:
                    collector.record_usage(**{key: usage.get(key) for key in (
                        "prompt_tokens", "completion_tokens", "cache_hit_tokens",
                        "cache_miss_tokens", "reasoning_tokens",
                    )}, kind=kind, complete=completed, call=started)
                    cancelled = isinstance(error, asyncio.CancelledError) or getattr(error, "code", None) in {
                        "codex_cancelled", "chatgpt_cancelled", "selection_cancelled",
                    }
                    collector.request_finished(
                        started, kind=kind, succeeded=succeeded,
                        status_code=getattr(error, "status_code", None),
                        error_type=type(error).__name__ if error else None,
                        provider_code=getattr(error, 'code', None),
                        finish_reason="completed" if succeeded else "cancelled" if cancelled else "failed",
                        protocol=(self.client.protocol if isinstance(getattr(self.client, 'protocol', None), str)
                                  else self.default_protocol), visible_chars=len(text) if text else None,
                        batch_size=params.get("batch_size"),
                    )
        finally:
            self._request_slots.release()
