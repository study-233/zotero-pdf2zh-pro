from __future__ import annotations

import json
import copy
import logging
import math
import os
import sqlite3
from contextlib import closing
from pathlib import Path
import threading
import time
import uuid
from collections import Counter, deque
from typing import Any, Callable

METRIC_LOGGER = logging.getLogger("zotero_pdf2zh_server.metrics")
MetricsCallback = Callable[[dict[str, Any]], None]
REQUEST_KINDS = ("translation", "review", "initialization")
REQUEST_PURPOSES = (*REQUEST_KINDS, "term_extraction")


def benchmark_journal(task_id):
    """Explicit benchmark-only ledger, separate from bounded product diagnostics."""
    directory = os.environ.get('PDF2ZH_BENCHMARK_LEDGER_DIR')
    if not directory:
        return None
    if not task_id or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in task_id):
        raise ValueError('Invalid benchmark task identifier')
    path = Path(directory) / (task_id + '.jsonl')
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()

    def record(event):
        with lock, path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(event, ensure_ascii=False, separators=(',', ':')) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
    return record


def reserve_benchmark_request():
    directory, limit = os.environ.get('PDF2ZH_BENCHMARK_LEDGER_DIR'), os.environ.get('PDF2ZH_BENCHMARK_MAX_REQUESTS')
    if not directory or not limit:
        return
    with closing(sqlite3.connect(Path(directory) / 'budget.sqlite3', timeout=10)) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        connection.execute('CREATE TABLE IF NOT EXISTS budget (id INTEGER PRIMARY KEY CHECK(id=1), calls INTEGER NOT NULL)')
        connection.execute('INSERT OR IGNORE INTO budget VALUES (1, 0)')
        count = connection.execute('SELECT calls FROM budget WHERE id=1').fetchone()[0]
        if count >= int(limit):
            raise RuntimeError('benchmark_request_limit')
        connection.execute('UPDATE budget SET calls=calls+1 WHERE id=1')


class RequestHandle(float):
    """A timestamp-compatible, unique handle; concurrent starts may share a clock tick."""

    def __new__(cls, timestamp, call_id, purpose):
        value = super().__new__(cls, timestamp)
        value.call_id, value.purpose = call_id, purpose
        value.finished = value.usage_recorded = value.validated = False
        return value


def _purpose(value):
    return value if value in REQUEST_PURPOSES else "translation"


def _kind(value):
    return value if value in REQUEST_KINDS else "translation"


def _number(value):
    return (int(value) if isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0 else None)


class _RequestStats:
    def __init__(self):
        self.attempts = self.succeeded = self.failed = self.active = self.retries = 0
        self.cancelled = 0
        self.repairs = 0
        self.latency_total = 0.0
        self.latencies = deque(maxlen=200)
        self.status_codes, self.errors, self.finishes, self.protocols, self.batch_sizes = (Counter() for _ in range(5))
        self.visible_chars = self.visible_known = 0

    def finish(self, *, succeeded, latency_ms, status_code=None, error_type=None,
               finish_reason=None, protocol=None, visible_chars=None, batch_size=None):
        self.succeeded += bool(succeeded)
        self.cancelled += finish_reason == "cancelled"
        self.failed += not succeeded and finish_reason != "cancelled"
        self.latency_total += latency_ms
        self.latencies.append(latency_ms)
        self.status_codes[str(status_code) if status_code is not None else "unknown"] += 1
        if error_type:
            self.errors[error_type] += 1
        if finish_reason:
            self.finishes[finish_reason] += 1
        if protocol:
            self.protocols[protocol] += 1
        if visible_chars is not None:
            self.visible_chars += visible_chars
            self.visible_known += 1
        if batch_size is not None:
            self.batch_sizes[str(batch_size)] += 1

    def snapshot(self):
        total = self.succeeded + self.failed + self.cancelled
        ordered = sorted(self.latencies)
        return {
            "attempts": self.attempts, "succeeded": self.succeeded, "failed": self.failed, "cancelled": self.cancelled,
            "active": self.active, "retries": self.retries, "repairs": self.repairs,
            "averageLatencyMs": round(self.latency_total / total, 1) if total else None,
            "p95LatencyMs": round(ordered[max(math.ceil(len(ordered) * .95) - 1, 0)], 1) if ordered else None,
            "latencyTotalMs": self.latency_total,
            "latencySamplesMs": list(self.latencies),
            "statusCodes": dict(self.status_codes), "errorTypes": dict(self.errors),
            "finishReasons": dict(self.finishes), "protocols": dict(self.protocols),
            "visibleOutputChars": self.visible_chars if self.visible_known else None,
            "batchSizes": dict(self.batch_sizes),
        }


class _TokenTotals:
    def __init__(self):
        self.calls = 0
        self.incomplete_calls = 0
        self.values = Counter()
        self.known = Counter()

    def record(self, *, prompt_tokens, completion_tokens, cache_hit_tokens, cache_miss_tokens,
               reasoning_tokens=None, complete=True):
        prompt, completion, hit, miss, reasoning = map(_number, (
            prompt_tokens, completion_tokens, cache_hit_tokens, cache_miss_tokens, reasoning_tokens))
        if prompt is not None and hit is not None:
            hit = min(hit, prompt)
            miss = prompt - hit
        elif prompt is not None and miss is not None:
            miss = min(miss, prompt)
            hit = prompt - miss
        if reasoning is not None and completion is not None:
            reasoning = min(reasoning, completion)
        self.calls += 1
        self.incomplete_calls += not complete
        for key, value in (("input", prompt), ("output", completion), ("reasoning", reasoning)):
            if value is not None:
                self.values[key] += value
                self.known[key] += 1
        if hit is not None and miss is not None:
            self.values["hit"] += hit
            self.values["miss"] += miss
            self.known["cache"] += 1

    def availability(self, *keys):
        counts = [self.known[key] for key in keys]
        if not any(counts):
            return "unavailable"
        return "complete" if not self.incomplete_calls and all(count == self.calls for count in counts) else "partial"

    def snapshot(self):
        return {
            "input": self.values["input"] if self.known["input"] else None,
            "output": self.values["output"] if self.known["output"] else None,
            "total": self.values["input"] + self.values["output"]
            if self.known["input"] or self.known["output"] else None,
            "reasoning": self.values["reasoning"] if self.known["reasoning"] else None,
            "availability": self.availability("input", "output"),
            "reasoningAvailability": self.availability("reasoning"),
        }

    def cache_snapshot(self):
        total = self.values["hit"] + self.values["miss"]
        return {
            "hitTokens": self.values["hit"] if self.known["cache"] else None,
            "missTokens": self.values["miss"] if self.known["cache"] else None,
            "hitRate": round(self.values["hit"] / total, 4) if total else None,
            "availability": self.availability("cache"),
        }

# Presets whose settings transform into the common OpenAI translator.
REQUEST_METRIC_SERVICES = frozenset(
    {
        "openai",
        "codex",
        "chatgpt",
        "openaicompatible",
        "deepseek",
        "aliyundashscope",
        "modelscope",
        "zhipu",
        "gemini",
        "grok",
        "groq",
    }
)


def supports_request_metrics(service: str) -> bool:
    return service.lower() in REQUEST_METRIC_SERVICES


def empty_metrics() -> dict[str, Any]:
    return {
        "requests": {**_RequestStats().snapshot(), "qps10s": 0.0,
                     "byKind": {kind: _RequestStats().snapshot() for kind in REQUEST_KINDS}},
        "localCache": {"hits": 0, "misses": 0, "hitRate": None},
        "providerCache": {
            "hitTokens": None,
            "missTokens": None,
            "hitRate": None,
            "availability": "unavailable",
        },
        "tokens": {**_TokenTotals().snapshot(),
                   "byKind": {kind: _TokenTotals().snapshot() for kind in REQUEST_KINDS}},
        "stageDurations": {},
        "throughput": {"paragraphsPerMinute": None, "etaSeconds": None},
        "referencesSkipped": 0,
    }


def merge_metrics(previous, current):
    """Merge a fixed pre-run baseline with a cumulative current-run snapshot.

    Repeated snapshots replace the current run instead of accumulating it twice.
    Rates, active work and ETA belong to the current run; counters span resumes.
    """
    before, latest = previous or {}, current or empty_metrics()
    result = copy.deepcopy(latest)

    def optional_sum(a, b):
        return None if a is None and b is None else (a or 0) + (b or 0)

    def counts(a, b):
        return dict(Counter(a or {}) + Counter(b or {}))

    def requests(a, b):
        a, b = a or {}, b or {}
        value = copy.deepcopy(b)
        for key in ('attempts', 'succeeded', 'failed', 'cancelled', 'retries', 'repairs'):
            value[key] = a.get(key, 0) + b.get(key, 0)
        value['active'] = b.get('active', 0)
        for key in ('statusCodes', 'errorTypes', 'finishReasons', 'protocols', 'batchSizes'):
            value[key] = counts(a.get(key), b.get(key))
        value['visibleOutputChars'] = optional_sum(a.get('visibleOutputChars'), b.get('visibleOutputChars'))
        finished = lambda row: sum(row.get(k, 0) for k in ('succeeded', 'failed', 'cancelled'))
        old_count, new_count = finished(a), finished(b)
        def latency_total(row):
            if not finished(row):
                return 0
            if row.get('latencyTotalMs') is not None:
                return row['latencyTotalMs']
            average = row.get('averageLatencyMs')
            return average * finished(row) if average is not None else None
        totals = [latency_total(row) for row in (a, b)]
        total_ms = sum(totals) if all(value is not None for value in totals) else None
        value['latencyTotalMs'] = total_ms
        value['averageLatencyMs'] = round(total_ms / (old_count + new_count), 1) if old_count + new_count and total_ms is not None else None
        samples = [*(a.get('latencySamplesMs') or []), *(b.get('latencySamplesMs') or [])][-200:]
        value['latencySamplesMs'] = samples
        if not new_count:
            value['p95LatencyMs'] = a.get('p95LatencyMs')
        elif old_count and not a.get('latencySamplesMs') and len(samples) < 200:
            # An old percentile cannot be combined accurately with a new one.
            value['p95LatencyMs'] = None
        else:
            ordered = sorted(samples)
            value['p95LatencyMs'] = round(ordered[max(math.ceil(len(ordered) * .95) - 1, 0)], 1) if ordered else b.get('p95LatencyMs')
        for key in ('byKind', 'byPurpose'):
            kinds = set(a.get(key, {})) | set(b.get(key, {}))
            if kinds:
                value[key] = {kind: requests(a.get(key, {}).get(kind), b.get(key, {}).get(kind)) for kind in kinds}
        return value

    def availability(a, b, key, old_calls, new_calls):
        old, new = a.get(key, 'unavailable'), b.get(key, 'unavailable')
        if not old_calls:
            return new
        if not new_calls:
            return old
        return old if old == new and old in {'complete', 'unavailable'} else 'partial'

    def tokens(a, b, old_requests, new_requests):
        a, b = a or {}, b or {}
        value = {key: optional_sum(a.get(key), b.get(key)) for key in ('input', 'output', 'total', 'reasoning')}
        for key in ('availability', 'reasoningAvailability'):
            value[key] = availability(a, b, key, old_requests.get('attempts', 0), new_requests.get('attempts', 0))
        for key in ('byKind', 'byPurpose'):
            kinds = set(a.get(key, {})) | set(b.get(key, {}))
            if kinds:
                value[key] = {kind: tokens(a.get(key, {}).get(kind), b.get(key, {}).get(kind),
                    old_requests.get(key, {}).get(kind, {}), new_requests.get(key, {}).get(kind, {})) for kind in kinds}
        return value

    old_requests, new_requests = before.get('requests', {}), latest.get('requests', {})
    result['requests'] = requests(old_requests, new_requests)
    result['requests']['qps10s'] = new_requests.get('qps10s', 0)
    result['tokens'] = tokens(before.get('tokens'), latest.get('tokens'), old_requests, new_requests)
    for key, fields in (('localCache', ('hits', 'misses')), ('providerCache', ('hitTokens', 'missTokens'))):
        a, b = before.get(key, {}), latest.get(key, {})
        values = {field: optional_sum(a.get(field), b.get(field)) for field in fields}
        total = sum(value or 0 for value in values.values())
        values['hitRate'] = round((values[fields[0]] or 0) / total, 4) if total else None
        if key == 'providerCache':
            values['availability'] = availability(a, b, 'availability', old_requests.get('attempts', 0), new_requests.get('attempts', 0))
        result[key] = values
    for key in ('validation', 'stageDurations'):
        result[key] = counts(before.get(key), latest.get(key))
    result['referencesSkipped'] = before.get('referencesSkipped', 0) + latest.get('referencesSkipped', 0)
    result['firstParagraphMs'] = before.get('firstParagraphMs') if before.get('firstParagraphMs') is not None else latest.get('firstParagraphMs')
    result['journal'] = {'enabled': bool(before.get('journal', {}).get('enabled') or latest.get('journal', {}).get('enabled')),
                         'errors': before.get('journal', {}).get('errors', 0) + latest.get('journal', {}).get('errors', 0)}
    result['currentExecution'] = copy.deepcopy(latest)
    return result


class TaskMetricsCollector:
    """Thread-safe request and task metric aggregation with throttled snapshots."""

    def __init__(
        self,
        *,
        task_id: str,
        provider: str,
        model: str,
        callback: MetricsCallback | None = None,
        clock: Callable[[], float] = time.monotonic,
        journal: MetricsCallback | None = None,
        evidence=None,
    ) -> None:
        self.task_id = task_id
        self.provider = provider
        self.model = model
        self.callback = callback
        self.clock = clock
        self.journal = journal if journal is not None else benchmark_journal(task_id)
        self.evidence = evidence
        self._journal_errors = 0
        self._call_prefix = uuid.uuid4().hex
        self._call_sequence = 0
        self._lock = threading.RLock()
        self._started_at = clock()
        self._last_emit_at = float("-inf")
        self._attempt_starts: deque[float] = deque()
        self._requests = _RequestStats()
        self._requests_by_kind = {kind: _RequestStats() for kind in REQUEST_KINDS}
        self._requests_by_purpose = {kind: _RequestStats() for kind in REQUEST_PURPOSES}
        self._local_hits = 0
        self._local_misses = 0
        self._usage = _TokenTotals()
        self._usage_by_kind = {kind: _TokenTotals() for kind in REQUEST_KINDS}
        self._usage_by_purpose = {kind: _TokenTotals() for kind in REQUEST_PURPOSES}
        self._validation = Counter(accepted=0, partial=0, rejected=0)
        self._translation_summary = None
        self._first_paragraph_ms = None
        self._stage_started = {}
        self._stage_durations = Counter()
        self._progress_samples: deque[tuple[float, float]] = deque()
        self._paragraph_samples: deque[tuple[float, int]] = deque()
        self._last_translation_current: int | None = None
        self._references_skipped = 0
        self._activity = Counter(queued=0, retrying=0, fallbackPending=0)
        self._active_requests = Counter()
        self._last_paragraph_completed = None

    def _journal(self, event, **fields):
        if self.journal is not None:
            try:
                self.journal({"event": event, "taskId": self.task_id,
                              "timestamp": time.time(), "elapsedMs": max(0, (self.clock() - self._started_at) * 1000),
                              "provider": self.provider, "model": self.model, **fields})
            except Exception:
                self._journal_errors += 1
                METRIC_LOGGER.warning("benchmark journal write failed")

    def request_started(self, *, kind="translation", operation_id=None, paragraph_ids=None, paragraph_refs=None,
                        parent_call=None, retry_cause=None) -> RequestHandle:
        reserve_benchmark_request()
        now = self.clock()
        with self._lock:
            self._call_sequence += 1
            handle = RequestHandle(now, f"{self._call_prefix}:{self._call_sequence}", _purpose(kind))
            for stats in (self._requests, self._requests_by_kind[_kind(kind)], self._requests_by_purpose[_purpose(kind)]):
                stats.attempts += 1
                stats.active += 1
                stats.repairs += retry_cause in {"batch_partial", "batch_fallback"}
            self._attempt_starts.append(now)
            self._active_requests[handle.call_id] = now
            self._journal("request_started", callId=handle.call_id, purpose=handle.purpose,
                          operationId=operation_id, paragraphIds=paragraph_ids or [],
                          parentCallId=getattr(parent_call, 'call_id', None), retryCause=retry_cause)
            if self.evidence is not None:
                self.evidence.request_started(handle, operation_id, paragraph_refs, parent_call, retry_cause)
            self._trim_locked(now)
        self._emit_if_due()
        return handle

    def request_finished(
        self,
        started_at: float,
        *,
        succeeded: bool,
        status_code: int | None = None,
        kind="translation", error_type=None, finish_reason=None, protocol=None,
        visible_chars=None, batch_size=None,
        provider_code=None,
    ) -> None:
        now = self.clock()
        with self._lock:
            if isinstance(started_at, RequestHandle):
                if started_at.finished:
                    return
                started_at.finished = True
                kind = started_at.purpose
                self._active_requests.pop(started_at.call_id, None)
                if not started_at.usage_recorded:
                    self.record_usage(prompt_tokens=None, completion_tokens=None, cache_hit_tokens=None,
                                      cache_miss_tokens=None, kind=kind, complete=False, call=started_at)
                self._journal("request_finished", callId=started_at.call_id, purpose=kind,
                              succeeded=succeeded, cancelled=finish_reason == 'cancelled',
                              latencyMs=max((now - started_at) * 1000, 0), statusCode=status_code)
                if self.evidence is not None:
                    self.evidence.request_finished(started_at,
                        requestState='succeeded' if succeeded else 'cancelled' if finish_reason == 'cancelled' else 'failed',
                        latencyMs=max((now - started_at) * 1000, 0), statusCode=status_code,
                        errorType=error_type, finishReason=finish_reason, protocol=protocol,
                        visibleOutputChars=visible_chars, batchSize=batch_size, providerCode=provider_code)
        latency_ms = max((now - started_at) * 1000, 0.0)
        self._finish_request(kind=kind, latency_ms=latency_ms, succeeded=succeeded,
                             status_code=status_code, error_type=error_type, finish_reason=finish_reason,
                             protocol=protocol, visible_chars=visible_chars, batch_size=batch_size)

    def record_completed_request(self, *, kind, latency_ms, succeeded, status_code=None,
                                 error_type=None, finish_reason=None, protocol=None,
                                 visible_chars=None, batch_size=None, provider_code=None):
        """Replay completed initialization without fabricating current active/QPS."""
        with self._lock:
            for stats in (self._requests, self._requests_by_kind[_kind(kind)], self._requests_by_purpose[_purpose(kind)]):
                stats.attempts += 1
            if self.evidence is not None:
                self._call_sequence += 1
                observed = RequestHandle(self.clock(), f'{self._call_prefix}:{self._call_sequence}', kind)
                self.evidence.request_started(observed, None, [], None, None)
                self.evidence.request_finished(observed, observedReplay=True,
                    requestState='succeeded' if succeeded else 'failed', latencyMs=latency_ms,
                    statusCode=status_code, errorType=error_type, finishReason=finish_reason,
                    protocol=protocol, visibleOutputChars=visible_chars, batchSize=batch_size, providerCode=provider_code)
        self._finish_request(kind=kind, latency_ms=latency_ms, succeeded=succeeded,
                             status_code=status_code, error_type=error_type, finish_reason=finish_reason,
                             protocol=protocol, visible_chars=visible_chars, batch_size=batch_size, replay=True)

    def _finish_request(self, *, kind, latency_ms, succeeded, status_code, error_type,
                        finish_reason, protocol, visible_chars, batch_size, replay=False):
        purpose, kind = _purpose(kind), _kind(kind)
        status_code = status_code if type(status_code) is int and 100 <= status_code <= 599 else None
        error_type = error_type if isinstance(error_type, str) and error_type.isidentifier() and len(error_type) <= 80 else None
        finish_reason = (finish_reason if finish_reason in {
            "stop", "length", "content_filter", "tool_calls", "function_call", "completed",
            "incomplete", "failed", "cancelled", "queued", "in_progress", "max_output_tokens",
        } else "other" if finish_reason is not None else None)
        protocol = protocol if protocol in {"chat_completions", "responses", "codex_app_server"} else None
        visible_chars, batch_size = _number(visible_chars), _number(batch_size)
        latency_ms = max(float(latency_ms), 0.0) if math.isfinite(float(latency_ms)) else 0.0
        with self._lock:
            for stats in (self._requests, self._requests_by_kind[kind], self._requests_by_purpose[purpose]):
                if not replay:
                    stats.active = max(stats.active - 1, 0)
                stats.finish(succeeded=succeeded, latency_ms=latency_ms, status_code=status_code,
                             error_type=error_type, finish_reason=finish_reason, protocol=protocol,
                             visible_chars=visible_chars, batch_size=batch_size)
        METRIC_LOGGER.info(
            "metric=%s",
            json.dumps(
                {
                    "taskId": self.task_id,
                    "provider": self.provider,
                    "model": self.model,
                    "event": "request",
                    "kind": kind,
                    "success": succeeded,
                    "statusCode": status_code,
                    "errorType": error_type, "finishReason": finish_reason,
                    "protocol": protocol, "visibleOutputChars": visible_chars,
                    "batchSize": batch_size,
                    "latencyMs": round(latency_ms, 1),
                },
                separators=(",", ":"),
            ),
        )
        self._emit_if_due()

    def retry_scheduled(self, *, kind="translation") -> None:
        with self._lock:
            self._requests.retries += 1
            self._requests_by_kind[_kind(kind)].retries += 1
            self._requests_by_purpose[_purpose(kind)].retries += 1
        self._emit_if_due()

    def local_cache_hit(self) -> None:
        with self._lock:
            self._local_hits += 1
        self._emit_if_due()

    def local_cache_miss(self) -> None:
        with self._lock:
            self._local_misses += 1
        self._emit_if_due()

    def record_usage(
        self,
        *,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        cache_hit_tokens: int | None,
        cache_miss_tokens: int | None,
        reasoning_tokens: int | None = None,
        kind="translation",
        complete=True,
        call=None,
    ) -> None:
        with self._lock:
            values = dict(prompt_tokens=_number(prompt_tokens), completion_tokens=_number(completion_tokens),
                          cache_hit_tokens=_number(cache_hit_tokens), cache_miss_tokens=_number(cache_miss_tokens),
                          reasoning_tokens=_number(reasoning_tokens), complete=complete)
            previous = None
            if isinstance(call, RequestHandle):
                previous = getattr(call, 'usage_values', None)
                if previous is not None:
                    # Provider usage notifications are cumulative. Repeated or older
                    # snapshots must not add cost or discard a known later total.
                    for key in values:
                        if key != 'complete' and previous[key] is not None:
                            values[key] = max(previous[key], values[key] or 0)
                    values['complete'] = previous['complete'] or complete
                if values == previous:
                    return
                call.usage_recorded = True
                call.usage_values = values
                kind = call.purpose
                self._journal("usage", callId=call.call_id, purpose=kind, complete=values['complete'],
                              input=values['prompt_tokens'], output=values['completion_tokens'],
                              cachedInput=values['cache_hit_tokens'], reasoning=values['reasoning_tokens'])
                if self.evidence is not None:
                    self.evidence.request_finished(call, input=values['prompt_tokens'],
                        output=values['completion_tokens'], reasoning=values['reasoning_tokens'])
            for totals in (self._usage, self._usage_by_kind[_kind(kind)], self._usage_by_purpose[_purpose(kind)]):
                if previous is not None:
                    old = _TokenTotals()
                    old.record(**previous)
                    totals.calls -= old.calls
                    totals.incomplete_calls -= old.incomplete_calls
                    totals.values.subtract(old.values)
                    totals.known.subtract(old.known)
                totals.record(**values)
        self._emit_if_due()

    def record_validation(self, call, status, *, accepted=None, rejected=None, code=None, invalid_outputs=None):
        if not isinstance(call, RequestHandle) or status not in {'accepted', 'partial', 'rejected'}:
            return
        with self._lock:
            if call.validated:
                return
            call.validated = True
            self._validation[status] += 1
            self._journal('validation', callId=call.call_id, status=status,
                          accepted=_number(accepted), rejected=_number(rejected))
            if self.evidence is not None:
                self.evidence.validation(call, status, code=code, rejected=invalid_outputs)
        self._emit_if_due()

    def cache_reused(self, operation_id, paragraph_refs):
        if self.evidence is not None:
            self.evidence.cache_reused(operation_id, paragraph_refs)

    def set_translation_summary(self, summary):
        with self._lock:
            self._translation_summary = {k: _number(summary.get(k)) for k in ('total', 'succeeded', 'failed', 'skipped', 'pending')}
            self._journal('translation_summary', **self._translation_summary)

    def first_paragraph_completed(self):
        with self._lock:
            if self._first_paragraph_ms is None:
                self._first_paragraph_ms = max(0, (self.clock() - self._started_at) * 1000)
                self._journal('first_paragraph_completed')

    def record_stage(self, name: str, seconds: float) -> None:
        if not isinstance(name, str) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds < 0:
            return
        with self._lock:
            self._stage_durations[name] += seconds
        self._emit_if_due()

    def update_progress(self, event: dict[str, Any]) -> None:
        if event.get("type") not in {
            "progress_start",
            "progress_update",
            "progress_end",
        }:
            return
        now = self.clock()
        try:
            overall = max(0.0, min(float(event.get("overall_progress") or 0), 100.0))
        except (TypeError, ValueError):
            overall = 0.0
        with self._lock:
            self._progress_samples.append((now, overall))
            stage = str(event.get("stage") or "")
            if stage:
                if event.get("type") == "progress_start" or stage not in self._stage_started:
                    self._stage_started[stage] = now
                completed = event.get("type") == "progress_end"
                if stage in {"Check Fonts", "Download Fonts"}:
                    try:
                        completed = completed or float(event.get("stage_current", -1)) >= float(event["stage_total"])
                    except (KeyError, ValueError, TypeError):
                        pass
                if completed:
                    self._stage_durations[stage] += max(now - self._stage_started.pop(stage), 0.0)
            if stage == "Translate Paragraphs":
                try:
                    current = max(int(event.get("stage_current") or 0), 0)
                except (TypeError, ValueError):
                    current = 0
                if self._last_translation_current is None:
                    self._last_translation_current = current
                elif current > self._last_translation_current:
                    self._last_paragraph_completed = now
                    self._paragraph_samples.append(
                        (now, current - self._last_translation_current)
                    )
                    self._last_translation_current = current
            self._trim_locked(now)
        self._emit_if_due()

    def activity_changed(self, name, delta):
        with self._lock:
            self._activity[name] = max(0, self._activity[name] + delta)
        self._emit_if_due()

    def reference_skipped(self, count: int = 1) -> None:
        with self._lock:
            self._references_skipped += max(int(count), 0)
        self._emit_if_due()

    def snapshot(self) -> dict[str, Any]:
        now = self.clock()
        with self._lock:
            self._trim_locked(now)
            local_total = self._local_hits + self._local_misses
            paragraphs = sum(value for _, value in self._paragraph_samples)
            throughput_window = min(max(now - self._started_at, 1.0), 30.0)
            throughput = (
                paragraphs * 60.0 / throughput_window
                if self._paragraph_samples
                else None
            )
            eta = self._eta_locked(now)
            metrics = empty_metrics()
            metrics["requests"] = {**self._requests.snapshot(),
                "byKind": {kind: stats.snapshot() for kind, stats in self._requests_by_kind.items()},
                "byPurpose": {kind: stats.snapshot() for kind, stats in self._requests_by_purpose.items()},
                "qps10s": round(len(self._attempt_starts) / 10.0, 2),
            }
            metrics["localCache"] = {
                "hits": self._local_hits,
                "misses": self._local_misses,
                "hitRate": (
                    round(self._local_hits / local_total, 4) if local_total else None
                ),
            }
            metrics["providerCache"] = self._usage.cache_snapshot()
            metrics["tokens"] = {**self._usage.snapshot(),
                                 "byKind": {kind: totals.snapshot() for kind, totals in self._usage_by_kind.items()},
                                 "byPurpose": {kind: totals.snapshot() for kind, totals in self._usage_by_purpose.items()}}
            metrics['validation'] = dict(self._validation)
            metrics['translationSummary'] = self._translation_summary
            metrics['firstParagraphMs'] = self._first_paragraph_ms
            metrics['journal'] = {'enabled': self.journal is not None, 'errors': self._journal_errors}
            metrics["stageDurations"] = {name: round(seconds, 6) for name, seconds in self._stage_durations.items()}
            metrics["throughput"] = {
                "paragraphsPerMinute": (
                    round(throughput, 1) if throughput is not None else None
                ),
                "etaSeconds": round(eta) if eta is not None else None,
            }
            metrics["referencesSkipped"] = self._references_skipped
            metrics["activity"] = {
                **self._activity,
                "oldestRequestSeconds": max(0, now - min(self._active_requests.values())) if self._active_requests else None,
                "lastParagraphCompletedAgoSeconds": max(0, now - self._last_paragraph_completed)
                    if self._last_paragraph_completed is not None else None,
            }
            return metrics

    def emit_final(self) -> None:
        self._emit(force=True)

    def emit_heartbeat(self) -> None:
        self._emit(force=False)

    def _eta_locked(self, now: float) -> float | None:
        if self._last_paragraph_completed is not None and now - self._last_paragraph_completed >= 30:
            return None
        elapsed = now - self._started_at
        if elapsed < 15 or len(self._progress_samples) < 2:
            return None
        latest_progress = self._progress_samples[-1][1]
        if latest_progress < 2:
            return None
        first_time, first_progress = self._progress_samples[0]
        duration = now - first_time
        delta = latest_progress - first_progress
        if duration <= 0 or delta <= 0:
            return None
        rate = delta / duration
        if rate <= 0:
            return None
        return max((100.0 - latest_progress) / rate, 0.0)

    def _trim_locked(self, now: float) -> None:
        while self._attempt_starts and now - self._attempt_starts[0] > 10:
            self._attempt_starts.popleft()
        while self._progress_samples and now - self._progress_samples[0][0] > 30:
            self._progress_samples.popleft()
        while self._paragraph_samples and now - self._paragraph_samples[0][0] > 30:
            self._paragraph_samples.popleft()

    def _emit_if_due(self) -> None:
        self._emit(force=False)

    def _emit(self, *, force: bool) -> None:
        callback = self.callback
        if callback is None:
            return
        now = self.clock()
        with self._lock:
            if not force and now - self._last_emit_at < 1:
                return
            self._last_emit_at = now
        try:
            callback(self.snapshot())
        except Exception as error:
            METRIC_LOGGER.warning(
                "metric_callback_failed taskId=%s errorType=%s",
                self.task_id,
                type(error).__name__,
            )
