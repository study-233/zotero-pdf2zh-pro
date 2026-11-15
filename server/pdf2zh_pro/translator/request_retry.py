"""Per-translator fragment request retries and cancellable provider backoff."""
import logging
import math
import random
import subprocess
import time
from email.utils import parsedate_to_datetime

import httpx
import openai
from tenacity import RetryError, retry

logger = logging.getLogger(__name__)


def _status_code(error) -> int | None:
    value = getattr(error, "status_code", None)
    if value is None:
        value = getattr(error, "http_status_code", None) or getattr(getattr(error, "response", None), "status_code", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _retry_params(state):
    return state.kwargs.get("rate_limit_params") or (state.args[2] if len(state.args) > 2 else {}) or {}


def _record_retry_before_sleep(retry_state) -> None:
    translator = retry_state.args[0] if retry_state.args else None
    collector = getattr(translator, "metrics_collector", None)
    params = _retry_params(retry_state)
    budget = params.get("request_budget")
    if budget is not None:
        budget.reserve_wait(retry_state.next_action.sleep)
    if collector is not None:
        collector.retry_scheduled(kind=params.get("metric_kind", "translation"))
    params['metric_retry_cause'] = 'provider_error'
    error = retry_state.outcome.exception() if retry_state.outcome else None
    logger.warning(
        "provider retry scheduled: error_type=%s status_code=%s attempt=%s",
        type(error).__name__ if error is not None else "unknown",
        _status_code(error),
        retry_state.attempt_number,
    )
    if translator is not None:
        if collector is not None:
            collector.activity_changed("retrying", 1)
        try:
            translator._wait_cancel(retry_state.next_action.sleep)
        finally:
            if collector is not None:
                collector.activity_changed("retrying", -1)


def _retry_provider_error(error: Exception) -> bool:
    if hasattr(error, "retryable"):
        return error.retryable and not getattr(error, "stop_task", False)
    status = _status_code(error)
    return (
        isinstance(error, (openai.APIConnectionError, httpx.TransportError, ConnectionError,
                           subprocess.TimeoutExpired))
        or isinstance(error.__cause__, subprocess.TimeoutExpired)
        or getattr(error, "should_retry", False)
        or bool(getattr(error, "retryable_verified_404", False))
        or status in {408, 409, 429}
        or (status is not None and status >= 500)
    )


def _stop_provider_retry(state) -> bool:
    params = _retry_params(state)
    budget = params.get("request_budget")
    count = state.args[0].retry_count
    if budget is None:
        return state.attempt_number >= (5 if count is None else count + 1)
    error = state.outcome.exception()
    return (state.attempt_number >= (2 if count is None else count + 1)
            or (params.get("batch_size", 1) > 1 and isinstance(error, openai.APITimeoutError))
            or not budget.available(state.upcoming_sleep))


def _retry_wait(state, backoff=None):
    args = getattr(state, "args", ())
    delay = args[0].retry_interval if args else None
    if delay is None:
        delay = backoff(state) if backoff is not None else min(30, 2 ** min(5, state.attempt_number)) + random.uniform(0, 1)
    error = state.outcome.exception()
    response = getattr(error, "response", None)
    value = response.headers.get("retry-after") if response is not None else getattr(error, "retry_after", None)
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


def configurable_retry(*, attempts, predicate, backoff):
    """Keep each adapter's legacy policy until an explicit override is supplied."""
    def custom(state):
        return state.args[0].custom_retry

    def stop(state):
        count = state.args[0].retry_count
        maximum = attempts if count is None else count + 1
        budget = _retry_params(state).get("request_budget")
        return state.attempt_number >= maximum or (custom(state) and budget is not None
                                                   and not budget.available(state.upcoming_sleep))

    def before_attempt(state):
        engine = state.args[0]
        if not engine.limits_each_attempt and (custom(state) or state.attempt_number > 1):
            params = _retry_params(state)
            engine.check_cancelled()
            engine.rate_limiter.wait({**params, "check_cancelled": engine.check_cancelled})
            budget = params.get("request_budget")
            if budget is not None and custom(state):
                budget.reserve()
            if notify := params.get("on_attempt"):
                notify()

    def before_sleep(state):
        if custom(state):
            _record_retry_before_sleep(state)
        else:
            state.args[0]._wait_cancel(state.next_action.sleep)

    def retry_error(state):
        if not custom(state):
            return predicate(state)
        return state.outcome.failed and _retry_provider_error(state.outcome.exception())

    def exhausted(state):
        if custom(state):
            return state.outcome.result()
        raise RetryError(state.outcome) from state.outcome.exception()

    return retry(retry=retry_error, stop=stop,
                 wait=lambda state: _retry_wait(state, backoff) if custom(state) else backoff(state),
                 before=before_attempt, before_sleep=before_sleep, sleep=lambda _: None,
                 retry_error_callback=exhausted)
