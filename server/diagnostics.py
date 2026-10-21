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

from diagnostic_contract import CONTRACT

SCHEMA_VERSION = CONTRACT['schemaVersion']
SCHEMA_REVISION = CONTRACT['schemaRevision']
MAX_EXPORT = 10 * 1024 * 1024
NUMBERS = set(CONTRACT['numeric'])
FLAGS = set(CONTRACT['flags'])
STRINGS = {key: set(values) for key, values in CONTRACT['enums'].items()}
IDENTIFIERS = set(CONTRACT['identifiers'])
LABELS = set(CONTRACT['labels'])
CONTAINERS = set(CONTRACT['containers'])
COUNT_MAPS = set(CONTRACT['countMaps'])


def safe_label(value):
    """Allow model IDs (including org/model:tag), not URLs, paths or credentials."""
    return (isinstance(value, str) and len(value) <= 200
            and re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*(?:[/:][a-zA-Z0-9][a-zA-Z0-9_.-]*)*', value)
            and not re.search(r'(?:sk-|Bearer|api[_-]?key|token=)', value, re.I))


def safe_counts(key, values, report=None):
    if not isinstance(values, dict): return {}
    enum_name = CONTRACT['countMaps'].get(key)
    allowed = STRINGS.get(enum_name, set()) if isinstance(enum_name, str) else set()
    result = {name: count for name, count in values.items()
            if isinstance(name, str) and (name in allowed or (key == 'statusCodes' and
                (name == 'unknown' or re.fullmatch(r'[1-5][0-9]{2}', name))))
            and type(count) is int and count >= 0}
    if report is not None: report['rejectedValues'] += len(values) - len(result)
    return result


def requested_configuration(payload):
    llm = payload.get('llm_api') or {}
    if not isinstance(llm, dict): llm = {}
    return safe_fields({'provider': payload.get('service'), 'model': llm.get('model'),
                        'protocol': llm.get('apiProtocol'), 'qps': payload.get('qps'),
                        'poolSize': payload.get('pool_size'), 'sourceLang': payload.get('source_lang'),
                        'targetLang': payload.get('target_lang')})


# Only fields listed here ever enter persistent diagnostics.
def safe_fields(data, _depth=0, *, report=None):
    if not isinstance(data, dict) or _depth > 8: return {}
    result = {}
    for key, value in data.items():
        if key in NUMBERS and isinstance(value, (int, float)) and not isinstance(value, bool):
            import math
            if math.isfinite(value): result[key] = value
        elif key in FLAGS and isinstance(value, bool): result[key] = value
        elif key in STRINGS and isinstance(value,str) and value in STRINGS[key]: result[key] = value
        elif key in CONTRACT['opaqueIdentifiers'] and isinstance(value, str) and re.fullmatch(CONTRACT['opaqueIdentifiers'][key], value): result[key] = value
        elif key in CONTRACT['identifierLists'] and isinstance(value, list):
            pattern = CONTRACT['opaqueIdentifiers'][CONTRACT['identifierLists'][key]]
            result[key] = [item for item in value[:256] if isinstance(item, str) and re.fullmatch(pattern, item)]
            if report is not None: report['rejectedValues'] += len(value) - len(result[key])
        elif key in LABELS and safe_label(value): result[key] = value
        elif key in COUNT_MAPS: result[key] = safe_counts(key, value, report)
        elif key in CONTAINERS and isinstance(value, dict): result[key] = safe_fields(value, _depth + 1, report=report)
        elif key in CONTAINERS and isinstance(value, list):
            def clean(item):
                if isinstance(item, list): return [safe_fields(row, _depth + 2, report=report) for row in item[:20000]]
                return safe_fields(item, _depth + 1, report=report)
            result[key] = [clean(item) for item in value[-20000:]]
        elif key == 'failedParagraphs' and isinstance(value, list):
            # No paragraph text, opaque document hashes, or free-form reason chains.
            result[key] = []
            for row in value[:2000]:
                if not isinstance(row, dict):
                    continue
                fields = {k: row[k] for k in CONTRACT['failedParagraphFields'] if k in row}
                if (row.get('errorType') == 'InvalidTranslation' and isinstance(row.get('reason'), str)
                        and not (isinstance(row.get('validationCode'), str) and row['validationCode'] in STRINGS['validationCode'])):
                    code = row['reason'].split(' → ', 1)[0]
                    if code in STRINGS['validationCode']:
                        fields['validationCode'] = code
                result[key].append(safe_fields(fields, _depth + 1, report=report))
            if len(value) > 2000: result['failedParagraphsTruncated'] = True
        elif key in IDENTIFIERS and isinstance(value, str) and re.fullmatch(r'[a-zA-Z0-9-]{1,64}', value): result[key] = value
        elif key in CONTRACT['timestamps'] and isinstance(value,str) and re.fullmatch(r'[0-9TZ:.+\-]{10,40}',value): result[key] = value
        elif key in CONTRACT['strings'] and isinstance(value, str) and re.fullmatch(r'[a-zA-Z0-9_. :+\-]{1,120}', value) and not re.search(r'\b(?:sk-|Bearer|api[_-]?key|token=)', value, re.I): result[key] = value
        elif report is not None:
            known = (key in NUMBERS or key in FLAGS or key in STRINGS or key in LABELS or key in IDENTIFIERS
                     or key in COUNT_MAPS or key in CONTRACT['timestamps'] or key in CONTRACT['strings']
                     or key in CONTRACT['opaqueIdentifiers'] or key in CONTRACT['identifierLists'])
            report['rejectedValues' if known else 'filteredFields'] += 1
    return result


def diagnostic_capabilities():
    return {'schemaVersions': CONTRACT['supportedSchemaVersions'],
            'schemaRevision': SCHEMA_REVISION, 'features': CONTRACT['features']}


def negotiate_schema(value):
    """Absent query preserves the shipped v1 interface; additions use revisions."""
    if value is None:
        return SCHEMA_VERSION
    # Bound user input before regex matching and integer conversion.
    if len(value) > 128:
        raise ValueError('invalid_schema_versions')
    if not re.fullmatch(r'[1-9][0-9]*(?:,[1-9][0-9]*){0,7}', value):
        raise ValueError('invalid_schema_versions')
    common = set(map(int, value.split(','))) & set(CONTRACT['supportedSchemaVersions'])
    return max(common) if common else None


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
        self.dropped_records = 0
        self.memory_evictions = 0
        self.filtered_fields = 0
        self.rejected_values = 0
        self.write_failures = 0
        self.oversized_records = 0
        self.pending = queue.Queue(maxsize=512)
        if self.root:
            threading.Thread(target=self._write_loop, daemon=True, name="diagnostic-writer").start()

    def record(self, event, data=None, *, frames=None):
        if not re.fullmatch(r'[a-z_]{1,48}', event): return
        report = {'filteredFields': 0, 'rejectedValues': 0}
        fields = safe_fields(data or {}, report=report)
        row = {**fields, 'time': datetime.now(timezone.utc).isoformat(), 'event': event}
        with self.lock:
            self.filtered_fields += report['filteredFields']
            self.rejected_values += report['rejectedValues']
        if frames is not None: row['frames'] = frames  # Only generated by exception_frames/thread_frames.
        encoded = json.dumps(row, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode()) > 128 * 1024:
            with self.lock:
                self.oversized_records += 1
                self.truncated = True
            return
        with self.lock:
            if len(self.memory) == self.memory.maxlen:
                self.memory_evictions += 1
            self.memory.append(row)
        if self.root:
            try:
                self.pending.put_nowait((row, encoded))
            except queue.Full:
                with self.lock:
                    self.dropped_records += 1
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
                with self.lock:
                    self.write_failures += 1
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
            collection = {'droppedRecords': self.dropped_records, 'memoryEvictions': self.memory_evictions, 'writeFailures': self.write_failures,
                          'oversizedRecords': self.oversized_records, 'filteredFields': self.filtered_fields,
                          'rejectedValues': self.rejected_values}
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
        return {'schemaVersion': SCHEMA_VERSION, 'schemaRevision': SCHEMA_REVISION, 'collection': collection, 'environment': {'python': platform.python_version(),
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
            elif record.levelno >= logging.WARNING or record.exc_info:
                data = safe_fields(getattr(record, 'diagnostic', {}))
                data.update(safe_fields({'file': Path(record.pathname).name,
                                         'function': record.funcName, 'line': record.lineno}))
                if record.exc_info:
                    error = record.exc_info[1]
                    data.update(safe_fields({'errorType': type(error).__name__,
                                            'validationCode': getattr(error, 'code', None),
                                            'providerCode': getattr(error, 'code', None)}))
                    self.emit_record('exception', data, frames=exception_frames(error))
                else:
                    if not data.get('validationCode') and not data.get('providerCode'):
                        data['collectionCode'] = 'unknown_warning'
                    self.emit_record('warning', data)
        except Exception:
            pass
