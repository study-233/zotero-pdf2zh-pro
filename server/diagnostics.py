"""Bounded diagnostic records. Never serialize arbitrary messages or request bodies."""
from __future__ import annotations
import json
import logging
import platform
import queue
import re
import sys
import threading
import time
import traceback
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
MAX_EXPORT = 10 * 1024 * 1024
NUMBERS = set('attempt attempts averageLatencyMs p95LatencyMs qps10s revision stageCurrent stageTotal stageProgress overallProgress currentPage selectedPage completedPages totalPages fontObject fontParseCount fontCacheHits operations idleSeconds stageElapsedSeconds latencyMs statusCode batchSize visibleOutputChars input output total active retries count pid cpuPercent rssBytes parentCpuPercent parentRssBytes exitCode elapsedSeconds droppedLogRecords promptTokens completionTokens totalTokens requestsUsed requestLimit succeeded failed pending translated cacheHits inFlight qps'.split())
FLAGS = {'cancelRequested', 'stalled', 'success', 'truncated', 'queueBlocked'}
NUMBERS.update('poolSize page skipped selected checked passed corrected unchecked notSelected requestsUsed requestLimit paragraphLimit hits misses hitRate hitTokens missTokens reasoning visibleOutputChars'.split())
FLAGS.update({'canRepair', 'canDownloadResult', 'failedParagraphsTruncated'})
STRINGS = {
    'stage': {'Queue Wait','Initialization','Check Fonts','Download Fonts','Font Preparation','Parse PDF and Create Intermediate Representation','Detect Scanned Pages','Parse Page Layout','Parse Table','Parse Paragraphs','Parse Formulas and Styles','Extract Terms','Translate Paragraphs','Typesetting','Add Fonts','Generate drawing instructions','Subset font','Save PDF','Finalize'},
    'retryReason': {'timeout','rate_limit','connection','server_error','invalid_output','other'},
    'errorType': {'ReadTimeout','ConnectTimeout','TimeoutError','APITimeoutError','RateLimitError','APIConnectionError','APIStatusError','AuthenticationError','PermissionDeniedError','NotFoundError','BadRequestError','InternalServerError','InvalidTranslation','UnprocessedParagraph','RuntimeError','ValueError','CancelledError','ConnectionError'},
    'finishReason': {'stop','length','content_filter','tool_calls','function_call','completed','incomplete','failed','cancelled','max_output_tokens','other'},
    'status': {'queued','running','cancelling','completed','incomplete','failed','cancelled'},
    'operation': {'page_start','page_end','font','resources','content_stream','initialization'},
    'cancelPhase': {'requested','terminating','killing','cancelled','cleanup_failed'},
    'cancelReason': {'user','forced','shutdown'},
    'kind': {'initialization','translation','term_extraction','review','repair'},
    'protocol': {'chat_completions','responses','auto'},
    'providerCode': {'model_not_found','invalid_api_key','insufficient_quota','rate_limit_exceeded','permission_denied'},
    'availability': {'complete','partial','unavailable'},
}
IDENTIFIERS = {'taskId', 'serverInstanceId'}
LABELS = {'provider', 'service', 'model', 'sourceLang', 'targetLang'}
CONTAINERS = set('requestedConfiguration effectiveConfiguration translationSummary qualitySummary metrics requests tokens byKind translation review initialization localCache providerCache'.split())
COUNT_MAPS = {'statusCodes', 'errorTypes', 'finishReasons', 'protocols'}


def safe_label(value):
    """Allow model IDs (including org/model:tag), not URLs, paths or credentials."""
    return (isinstance(value, str) and len(value) <= 200
            and re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*(?:[/:][a-zA-Z0-9][a-zA-Z0-9_.-]*)*', value)
            and not re.search(r'(?:sk-|Bearer|api[_-]?key|token=)', value, re.I))


def safe_counts(key, values):
    if not isinstance(values, dict): return {}
    allowed = {'errorTypes': STRINGS['errorType'], 'finishReasons': STRINGS['finishReason'],
               'protocols': STRINGS['protocol']}.get(key, set())
    return {name: count for name, count in values.items()
            if isinstance(name, str) and (name in allowed or (key == 'statusCodes' and
                (name == 'unknown' or re.fullmatch(r'[1-5][0-9]{2}', name))))
            and type(count) is int and count >= 0}


def requested_configuration(payload):
    llm = payload.get('llm_api') or {}
    if not isinstance(llm, dict): llm = {}
    return safe_fields({'provider': payload.get('service'), 'model': llm.get('model'),
                        'protocol': llm.get('apiProtocol'), 'qps': payload.get('qps'),
                        'poolSize': payload.get('pool_size'), 'sourceLang': payload.get('source_lang'),
                        'targetLang': payload.get('target_lang')})


# Only fields listed here ever enter persistent diagnostics.
def safe_fields(data, _depth=0):
    if not isinstance(data, dict) or _depth > 8: return {}
    result = {}
    for key, value in data.items():
        if key in NUMBERS and isinstance(value, (int, float)) and not isinstance(value, bool):
            import math
            if math.isfinite(value): result[key] = value
        elif key in FLAGS and isinstance(value, bool): result[key] = value
        elif key in STRINGS and isinstance(value,str) and value in STRINGS[key]: result[key] = value
        elif key in LABELS and safe_label(value): result[key] = value
        elif key in COUNT_MAPS: result[key] = safe_counts(key, value)
        elif key in CONTAINERS and isinstance(value, dict): result[key] = safe_fields(value, _depth + 1)
        elif key == 'failedParagraphs' and isinstance(value, list):
            # No paragraph text, opaque document hashes, or free-form reason chains.
            result[key] = [safe_fields({k: row[k] for k in ('page', 'attempts', 'errorType', 'statusCode', 'providerCode') if k in row}, _depth + 1)
                           for row in value[:2000] if isinstance(row, dict)]
            if len(value) > 2000: result['failedParagraphsTruncated'] = True
        elif key in IDENTIFIERS and isinstance(value, str) and re.fullmatch(r'[a-zA-Z0-9-]{1,64}', value): result[key] = value
        elif key in {'createdAt','updatedAt','lastProgressAt','heartbeatAt','lastRequestAt'} and isinstance(value,str) and re.fullmatch(r'[0-9TZ:.+\-]{10,40}',value): result[key] = value
    return result


def exception_frames(exc):
    # No exception message, source lines, locals or absolute paths.
    return [{'function': f.name[:100], 'file': Path(f.filename).name[:100], 'line': f.lineno}
            for f in traceback.extract_tb(exc.__traceback__)[-40:]]


def thread_frames():
    return [[{'function': f.name[:100], 'file': Path(f.filename).name[:100], 'line': f.lineno}
             for f in traceback.extract_stack(frame)[-30:]]
            for frame in list(sys._current_frames().values())[:16]]


class DiagnosticStore:
    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else None
        self.lock = threading.RLock()
        self.memory = deque(maxlen=1000)
        self.last_prune = 0.0
        self.active = set()
        self.truncated = False
        self.pending = queue.Queue(maxsize=512)
        if self.root:
            threading.Thread(target=self._write_loop, daemon=True, name="diagnostic-writer").start()

    def record(self, event, data=None, *, frames=None):
        if not re.fullmatch(r'[a-z_]{1,48}', event): return
        row = {'time': datetime.now(timezone.utc).isoformat(), 'event': event, **safe_fields(data or {})}
        if frames is not None: row['frames'] = frames  # Only generated by exception_frames/thread_frames.
        encoded = json.dumps(row, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode()) > 128 * 1024: return
        with self.lock:
            self.memory.append(row)
        if self.root:
            try:
                self.pending.put_nowait((row, encoded))
            except queue.Full:
                self.truncated = True

    def _write_loop(self):
        # Disk stalls must never hold the supervisor or task-state lock.
        while True:
            row, encoded = self.pending.get()
            try:
                self.root.mkdir(parents=True, exist_ok=True)
                task = row.get('taskId')
                if task:
                    self._append(f"task-{task}-{int(row.get('attempt', 1))}", encoded, 2 * 1024 * 1024, 1)
                else:
                    self._append('service', encoded, 5 * 1024 * 1024, 2)
                self._prune()
            except OSError:
                self.truncated = True
            finally:
                self.pending.task_done()

    def flush(self, timeout=.2):
        deadline = time.monotonic() + timeout
        while self.pending.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(.005)
        return not self.pending.unfinished_tasks

    def _append(self, name, line, limit, backups):
        path = self.root / f'{name}.jsonl'
        if path.exists() and path.stat().st_size + len(line.encode()) + 1 > limit:
            for i in range(backups, 0, -1):
                src = path if i == 1 else Path(f'{path}.{i-1}')
                if src.exists(): src.replace(Path(f'{path}.{i}'))
            self.truncated = True
        with path.open('a', encoding='utf-8') as out: out.write(line + '\n')

    def _prune(self):
        now = time.time()
        if now - self.last_prune < 30: return
        self.last_prune = now
        files = sorted(self.root.glob('*.jsonl*'), key=lambda p: p.stat().st_mtime)
        size = sum(p.stat().st_size for p in files)
        for p in files:
            if size <= 100 * 1024 * 1024 and now-p.stat().st_mtime <= 7*86400: continue
            if any(p.name.startswith(f'task-{task}-') for task in self.active): continue
            size -= p.stat().st_size
            p.unlink()
            self.truncated = True

    def export(self, task_id=None):
        # Snapshot under lock, disk read outside lock: exports cannot stall cancellation.
        with self.lock:
            memory = list(self.memory)
            truncated = self.truncated
        rows, size = [], 0
        if self.root and self.root.exists():
            paths = list(self.root.glob(f'task-{task_id}-*.jsonl*' if task_id else '*.jsonl*'))
            for path in sorted(paths, key=lambda p:p.stat().st_mtime, reverse=True):
                try:
                    with path.open('rb') as stream:
                        length=path.stat().st_size
                        stream.seek(max(0,length-MAX_EXPORT))
                        if length>MAX_EXPORT: stream.readline(); truncated=True
                        lines=stream.read(MAX_EXPORT).splitlines()
                    for line in reversed(lines):
                        if size+len(line)>MAX_EXPORT: truncated=True; break
                        try: row=json.loads(line)
                        except (ValueError,UnicodeError): continue
                        rows.append(row); size+=len(line)
                    if size>=MAX_EXPORT-128*1024: truncated=True; break
                except OSError: truncated=True
        # Include recent records even if disk persistence is slow or failed.
        known = {(r.get('time'), r.get('event')) for r in rows}
        for row in memory:
            if task_id and row.get('taskId') != task_id:
                continue
            if (row.get('time'), row.get('event')) not in known:
                rows.insert(0, row)
        rows.sort(key=lambda r: r.get('time', ''), reverse=True)
        kept, size = [], 0
        for row in rows:
            length = len(json.dumps(row, ensure_ascii=False).encode())
            if size + length > MAX_EXPORT:
                truncated = True
                break
            kept.append(row)
            size += length
        rows = kept
        return {'schemaVersion': SCHEMA_VERSION, 'environment': {'python': platform.python_version(),
                'os': platform.system(), 'architecture': platform.machine()},
                'truncated': truncated, 'records': list(reversed(rows))}


class SafeLogHandler(logging.Handler):
    def __init__(self, emit_record):
        super().__init__(); self.emit_record = emit_record

    def emit(self, record):
        try:
            if record.name == 'zotero_pdf2zh_server.metrics' and record.args:
                # Metrics logger passes JSON; filter labels as well as numeric fields.
                raw = record.args[0] if isinstance(record.args, tuple) else None
                if isinstance(raw,str):
                    data = json.loads(raw)
                    error = str(data.get('errorType', '')).lower()
                    if error:
                        data['retryReason'] = ('timeout' if 'timeout' in error else 'rate_limit' if 'ratelimit' in error
                                               else 'connection' if 'connection' in error else 'other')
                    self.emit_record('request',safe_fields(data))
            elif record.exc_info:
                self.emit_record('exception',{},frames=exception_frames(record.exc_info[1]))
            elif record.levelno >= logging.WARNING:
                self.emit_record('warning',{})
        except Exception:
            pass
