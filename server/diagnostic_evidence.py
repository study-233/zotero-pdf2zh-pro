"""Bounded, content-free request/paragraph evidence and offline diagnosis.

The worker owns the writer; export uses short read-only SQLite transactions.
This ledger is independent of rotating logs and never stores provider bodies.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import queue
import re
import sqlite3
import threading
import time
import uuid
import zipfile

from diagnostic_contract import CONTRACT
from diagnostics import safe_fields

FILENAME = 'diagnostic-evidence.sqlite3'
MAX_BYTES = 8 * 1024 * 1024
MAX_ROWS = 12000
MAX_EXECUTIONS = 32
MAX_EXPORT_BYTES = 4 * 1024 * 1024


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def _rows(value):
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def call_ref(handle):
    value = getattr(handle, 'call_id', '')
    return 'c-' + value.replace(':', '-') if re.fullmatch(r'[0-9a-f]{32}:[0-9]{1,12}', value) else None


class EvidenceLedger:
    def __init__(self, directory, task_id, attempt=1, *, max_bytes=MAX_BYTES, max_rows=MAX_ROWS):
        self.path = Path(directory) / FILENAME
        self.task_id, self.attempt = task_id, attempt
        self.execution_id = 'x-' + uuid.uuid4().hex
        self.max_bytes, self.max_rows = max_bytes, max_rows
        self.pending = queue.Queue(maxsize=512)
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.disabled = False
        self.counters = dict(queueDropped=0, storageErrors=0, unflushedEvents=0)
        self._operations = {}
        self._thread = threading.Thread(target=self._write_loop, daemon=True, name='diagnostic-evidence')
        try:
            self._thread.start()
        except RuntimeError:
            self.disabled = True
            self.counters['storageErrors'] += 1
        self.execution(executionState='running', createdAt=timestamp())

    def health(self):
        with self.lock:
            return {**self.counters, 'executionId': self.execution_id}

    def _submit(self, kind, data):
        if self.disabled or self.stop.is_set():
            with self.lock: self.counters['queueDropped'] += 1
            return
        try:
            self.pending.put_nowait((kind, data))
        except queue.Full:
            with self.lock: self.counters['queueDropped'] += 1

    def execution(self, **fields):
        self._submit('execution', safe_fields(fields))

    def operation(self, value):
        if isinstance(value, str) and re.fullmatch(r'[0-9a-f]{32}', value):
            return 'o-' + value
        # Legacy/custom operation labels stay in memory only; never export them.
        with self.lock:
            if len(self._operations) >= MAX_ROWS:
                self._operations.clear()
            return self._operations.setdefault(value, 'o-' + uuid.uuid4().hex) if isinstance(value, str) else 'o-' + uuid.uuid4().hex

    def request_started(self, handle, operation_id, paragraph_refs, parent_call, retry_cause):
        reference = call_ref(handle)
        if reference is None:
            return
        handle.evidence_operation = self.operation(operation_id)
        handle.evidence_paragraphs = safe_fields({'paragraphRefs': paragraph_refs or []}).get('paragraphRefs', [])
        omitted = max(0, len(paragraph_refs or []) - len(handle.evidence_paragraphs))
        self._submit('request_started', safe_fields({
            'callId': reference, 'operationId': handle.evidence_operation,
            'parentCallId': call_ref(parent_call), 'paragraphRefs': handle.evidence_paragraphs,
            'purpose': handle.purpose, 'retryCause': retry_cause if retry_cause in CONTRACT['enums']['retryCause'] else 'other' if retry_cause else 'none',
            'omittedLinks': omitted,
            'requestState': 'started', 'validationState': 'not_checked', 'createdAt': timestamp(),
        }))

    def request_finished(self, handle, **fields):
        self._submit('request', safe_fields({'callId': call_ref(handle), **fields}))

    def validation(self, handle, state, *, code=None, rejected=None):
        refs = getattr(handle, 'evidence_paragraphs', [])
        failures = rejected or {index: code for index in range(len(refs)) if code}
        failures = {index: reason if reason in CONTRACT['validationErrors'] else 'unknown_validation'
                    for index, reason in failures.items()}
        rejected_rows = [{'paragraphRef': refs[index], 'validationCode': reason}
                         for index, reason in failures.items() if type(index) is int and 0 <= index < len(refs)]
        self._submit('request', safe_fields({'callId': call_ref(handle), 'validationState': state,
                                          'validationCode': code, 'validationFailures': rejected_rows}))
        for index, reason in failures.items():
            if type(index) is int and 0 <= index < len(refs):
                self.paragraphs([{'paragraphRef': refs[index], 'validationCode': reason}])

    def cache_reused(self, operation_id, paragraph_refs):
        self._submit('cache', safe_fields({'operationId': self.operation(operation_id),
                                          'paragraphRefs': paragraph_refs or []}))

    def paragraphs(self, rows):
        clean = [safe_fields({key: row[key] for key in CONTRACT['evidenceFields']['paragraph'] if key in row})
                 for row in rows]
        for offset in range(0, len(clean), 64):
            self._submit('paragraphs', clean[offset:offset + 64])

    def close(self, status, summary=None, timeout=1, request_summary=None):
        if self.stop.is_set():
            return self.health()
        self.execution(executionState=status, translationSummary=summary or {},
                       **({'requestSummary': request_summary} if request_summary is not None else {}), **self.health())
        deadline = time.monotonic() + timeout
        while self.pending.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(.005)
        with self.lock:
            self.counters['unflushedEvents'] = self.pending.unfinished_tasks
        self.stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=max(0, deadline - time.monotonic()))
        return self.health()

    def _write_loop(self):
        connection = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path, timeout=.05)
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute('PRAGMA synchronous=NORMAL')
            connection.execute('PRAGMA journal_size_limit=1048576')
            connection.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            metadata = dict(connection.execute('SELECT key,value FROM metadata'))
            if metadata and (metadata.get('taskId') != self.task_id or metadata.get('version') != '1'):
                raise ValueError('invalid_evidence_store')
            connection.execute('CREATE TABLE IF NOT EXISTS records (kind TEXT, id TEXT, execution_id TEXT, payload TEXT, bytes INTEGER, priority INTEGER, sequence INTEGER, PRIMARY KEY(kind,id,execution_id))')
            with connection:
                for key, value in {'taskId': self.task_id, 'version': '1'}.items():
                    connection.execute('INSERT OR IGNORE INTO metadata VALUES (?,?)', (key, value))
                for key, payload in connection.execute("SELECT id,payload FROM records WHERE kind='execution'").fetchall():
                    row = json.loads(payload)
                    if row.get('executionState') == 'running':
                        row['executionState'] = 'interrupted'
                        encoded = json.dumps(row)
                        connection.execute("UPDATE records SET payload=?,bytes=? WHERE kind='execution' AND id=?", (encoded, len(encoded.encode()), key))
            used = connection.execute('SELECT COALESCE(SUM(bytes),0),COUNT(*) FROM records').fetchone()
            self._used, self._rows = used
            self._sequence = connection.execute('SELECT COALESCE(MAX(sequence),0) FROM records').fetchone()[0]
            while not self.stop.is_set() or not self.pending.empty():
                try:
                    kind, data = self.pending.get(timeout=.05)
                except queue.Empty:
                    continue
                try:
                    with connection:
                        self._apply(connection, kind, data)
                        self._trim(connection)
                except (sqlite3.Error, OSError, ValueError, TypeError):
                    with self.lock: self.counters['storageErrors'] += 1
                    self._used, self._rows = connection.execute('SELECT COALESCE(SUM(bytes),0),COUNT(*) FROM records').fetchone()
                finally:
                    self.pending.task_done()
        except (sqlite3.Error, OSError, ValueError, TypeError):
            self.disabled = True
            with self.lock: self.counters['storageErrors'] += 1
            while not self.pending.empty():
                self.pending.get_nowait(); self.pending.task_done()
                with self.lock: self.counters['queueDropped'] += 1
        finally:
            if connection is not None: connection.close()

    def _upsert(self, connection, kind, reference, fields):
        if not reference:
            return
        previous = connection.execute('SELECT payload,bytes FROM records WHERE kind=? AND id=? AND execution_id=?',
                                      (kind, reference, self.execution_id)).fetchone()
        row = json.loads(previous[0]) if previous else {}
        row.update(fields)
        if kind == 'paragraph' and fields.get('paragraphState') == 'succeeded':
            for key in ('validationCode', 'providerCode', 'errorType', 'statusCode', 'missingPlaceholderCount', 'extraPlaceholderCount'):
                row.pop(key, None)
        row.update(taskId=self.task_id, attempt=self.attempt, executionId=self.execution_id, updatedAt=timestamp())
        row = safe_fields({key: row[key] for key in CONTRACT['evidenceFields'][kind] if key in row})
        payload = json.dumps(row, separators=(',', ':'))
        size = len(payload.encode())
        if size > 32768:
            with self.lock: self.counters['queueDropped'] += 1
            return
        priority = 2 if kind == 'paragraph' and row.get('paragraphState') in {'failed', 'pending'} else int(
            row.get('requestState') in {'failed', 'cancelled'} or row.get('validationState') in {'partial', 'rejected'})
        self._sequence += 1
        connection.execute('INSERT INTO records VALUES (?,?,?,?,?,?,?) ON CONFLICT(kind,id,execution_id) DO UPDATE SET payload=excluded.payload,bytes=excluded.bytes,priority=excluded.priority,sequence=excluded.sequence',
                           (kind, reference, self.execution_id, payload, size, priority, self._sequence))
        self._used += size - (previous[1] if previous else 0)
        self._rows += not bool(previous)

    def _paragraph_link(self, connection, reference, *, call=None, operation=None, source=None):
        previous = connection.execute("SELECT payload FROM records WHERE kind='paragraph' AND id=? AND execution_id=?",
                                      (reference, self.execution_id)).fetchone()
        row = json.loads(previous[0]) if previous else {'paragraphRef': reference}
        for key, value in (('callRefs', call), ('operationRefs', operation)):
            values = row.setdefault(key, [])
            if value and value not in values:
                values.append(value)
                if len(values) > 16:
                    del values[0]; row['omittedLinks'] = row.get('omittedLinks', 0) + 1
        if source: row['outcomeSource'] = source
        self._upsert(connection, 'paragraph', reference, row)

    def _apply(self, connection, kind, data):
        if kind == 'execution':
            data.update(self.health())
            self._upsert(connection, 'execution', self.execution_id, data)
        elif kind in {'request', 'request_started'}:
            previous = connection.execute("SELECT payload FROM records WHERE kind='request' AND id=? AND execution_id=?",
                                          (data.get('callId'), self.execution_id)).fetchone()
            previous_state = json.loads(previous[0]).get('requestState') if previous else None
            self._upsert(connection, 'request', data.get('callId'), data)
            if kind == 'request_started':
                self._bump(connection, attempts=1, pending=1)
                for reference in data.get('paragraphRefs', []):
                    self._paragraph_link(connection, reference, call=data.get('callId'), operation=data.get('operationId'), source='request')
            elif data.get('requestState') in {'succeeded', 'failed', 'cancelled'} and previous_state != data['requestState']:
                self._bump(connection, **{data['requestState']: 1}, pending=-1 if previous_state == 'started' else 0)
        elif kind == 'paragraphs':
            for row in data:
                self._upsert(connection, 'paragraph', row.get('paragraphRef'), row)
        elif kind == 'cache':
            self._bump(connection, cacheHits=1)
            for reference in data.get('paragraphRefs', []):
                self._paragraph_link(connection, reference, operation=data.get('operationId'), source='cache')

    def _bump(self, connection, **counts):
        previous = connection.execute("SELECT payload FROM records WHERE kind='execution' AND id=?", (self.execution_id,)).fetchone()
        summary = json.loads(previous[0]).get('requestSummary', {}) if previous else {}
        for key, value in counts.items(): summary[key] = max(0, summary.get(key, 0) + value)
        self._upsert(connection, 'execution', self.execution_id, {'requestSummary': summary})

    def _trim(self, connection):
        old = connection.execute("SELECT id FROM records WHERE kind='execution' ORDER BY sequence DESC LIMIT -1 OFFSET ?", (MAX_EXECUTIONS,)).fetchall()
        for (execution,) in old:
            size, count = connection.execute('SELECT COALESCE(SUM(bytes),0),COUNT(*) FROM records WHERE execution_id=?', (execution,)).fetchone()
            connection.execute('DELETE FROM records WHERE execution_id=?', (execution,))
            self._used -= size; self._rows -= count
            self._omitted(connection, 'omittedExecutions')
        while self._used > self.max_bytes or self._rows > self.max_rows:
            row = connection.execute("SELECT rowid,kind,bytes FROM records WHERE kind!='execution' ORDER BY priority,sequence LIMIT 1").fetchone()
            if row is None: break
            connection.execute('DELETE FROM records WHERE rowid=?', (row[0],))
            self._used -= row[2]; self._rows -= 1
            self._omitted(connection, 'omittedRequests' if row[1] == 'request' else 'omittedParagraphs')

    @staticmethod
    def _omitted(connection, key):
        connection.execute("INSERT INTO metadata VALUES (?, '1') ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1", (key,))


def read_evidence(directory, task_id, task_attempt, task_status, *, max_bytes=MAX_EXPORT_BYTES):
    result = {'taskId': task_id, 'evidenceStatus': 'missing', 'evidenceCode': 'evidence_missing',
              'executions': [], 'requests': [], 'paragraphs': []}
    path = Path(directory) / FILENAME
    if not path.is_file(): return result
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=.05)) as connection:
            if dict(connection.execute('SELECT key,value FROM metadata')).get('taskId') != task_id:
                raise ValueError('invalid_evidence_store')
            metadata = dict(connection.execute('SELECT key,value FROM metadata'))
            if metadata.get('version') != '1': raise ValueError('invalid_evidence_store')
            rows = connection.execute("SELECT kind,payload FROM records ORDER BY CASE WHEN kind='execution' THEN 3 ELSE priority END DESC,sequence DESC LIMIT ?", (MAX_ROWS + MAX_EXECUTIONS,)).fetchall()
        result.update(evidenceStatus='available', evidenceCode='none')
        for key in ('omittedRequests', 'omittedParagraphs', 'omittedExecutions'):
            result[key] = max(0, int(metadata.get(key, 0)))
        consumed = 0
        for kind, payload in rows:
            row = safe_fields(json.loads(payload))
            if kind not in CONTRACT['evidenceFields'] or row.get('taskId') != task_id: continue
            row = {key: value for key, value in row.items() if key in CONTRACT['evidenceFields'][kind]}
            size = len(json.dumps(row).encode())
            if consumed + size > max_bytes:
                key = {'request': 'omittedRequests', 'paragraph': 'omittedParagraphs', 'execution': 'omittedExecutions'}[kind]
                result[key] += 1
                continue
            consumed += size
            result[{'execution': 'executions', 'request': 'requests', 'paragraph': 'paragraphs'}[kind]].append(row)
        executions = {row.get('executionId'): row for row in result['executions']}
        for row in executions.values():
            if row.get('executionState') == 'running' and (row.get('attempt', 0) < task_attempt or task_status in {'completed', 'incomplete', 'failed', 'cancelled'}):
                row['executionState'] = 'interrupted'
        for row in result['requests']:
            execution = executions.get(row.get('executionId'), {})
            if row.get('requestState') == 'started' and execution.get('executionState') in {'interrupted', 'cancelled', 'failed', 'completed', 'incomplete'}:
                row['requestState'] = 'interrupted'
        incomplete = any(result.get(key) for key in ('omittedRequests', 'omittedParagraphs', 'omittedExecutions')) or any(
            row.get(key) for row in executions.values() for key in ('queueDropped', 'storageErrors', 'unflushedEvents'))
        incomplete |= any(row.get('omittedLinks') for row in result['requests'] + result['paragraphs'])
        if incomplete:
            result.update(evidenceStatus='partial', evidenceCode='evidence_truncated')
        elif (any(row.get('executionState') == 'interrupted' for row in executions.values())
              or any(row.get('requestState') == 'interrupted' for row in result['requests'])):
            result.update(evidenceStatus='partial', evidenceCode='interrupted_execution')
        elif any(row.get('requestTracing') is False for row in executions.values()):
            result.update(evidenceStatus='partial', evidenceCode='writer_incomplete')
        return result
    except (sqlite3.Error, OSError, ValueError, TypeError, KeyError):
        result.update(evidenceStatus='unavailable', evidenceCode='invalid_evidence_store')
        return result


def link_failures(task, graph):
    """Use durable task outcomes even if the background ledger lost its tail."""
    requests = graph['requests']
    for failed in task.get('failedParagraphs', []):
        reference = failed.get('paragraphRef')
        if not reference: continue
        related = [row for row in requests if reference in row.get('paragraphRefs', [])
                   and row.get('attempt') == task.get('attempt')]
        calls = list(dict.fromkeys(row['callId'] for row in related if row.get('callId')))
        failed.update(callRefs=calls[-16:], omittedLinks=max(0, len(calls) - 16),
                      operationRefs=list(dict.fromkeys(row['operationId'] for row in related if row.get('operationId')))[-16:],
                      taskAttempt=task.get('attempt'))
        if not any(row.get('paragraphRef') == reference and row.get('attempt') == task.get('attempt')
                   and row.get('paragraphState') == 'failed' for row in graph['paragraphs']):
            graph['paragraphs'].append(safe_fields({**failed, 'taskId': task.get('taskId'),
                'attempt': task.get('attempt'), 'paragraphState': 'failed', 'recordOrigin': 'checkpoint_snapshot'}))
    calls = {row.get('callId') for row in requests}
    executions = {row.get('executionId') for row in graph['executions']}
    broken = any(row.get('parentCallId') and row['parentCallId'] not in calls for row in requests)
    broken |= any(row.get('executionId') not in executions for row in requests)
    broken |= any(ref not in calls for row in graph['paragraphs'] for ref in row.get('callRefs', []))
    broken |= any(not row.get('callRefs') for row in task.get('failedParagraphs', []))
    if graph['evidenceStatus'] == 'available' and broken:
        graph.update(evidenceStatus='partial', evidenceCode='writer_incomplete')
    return graph


def bound_evidence(graph, max_bytes=MAX_EXPORT_BYTES):
    """Bound checkpoint fallback additions as well as SQLite records."""
    consumed = 4096
    for field, counter in (('executions', 'omittedExecutions'), ('paragraphs', 'omittedParagraphs'),
                           ('requests', 'omittedRequests')):
        retained = []
        for row in graph[field]:
            size = len(json.dumps(row, ensure_ascii=False).encode()) + 2
            if consumed + size > max_bytes:
                graph[counter] = graph.get(counter, 0) + 1
                graph.update(evidenceStatus='partial', evidenceCode='evidence_truncated')
            else:
                retained.append(row)
                consumed += size
        graph[field] = retained
    return graph


def analyze(tasks, evidence):
    findings = []
    omitted = 0
    def finding(code, **fields):
        nonlocal omitted
        if len(findings) >= 2000:
            omitted += 1
            return
        findings.append(safe_fields({'findingCode': code, **fields}))
    if not isinstance(evidence, dict): evidence = {}
    for task in _rows(tasks):
        task_id = task.get('taskId')
        graph = next((row for row in _rows(evidence.get('taskEvidence')) if row.get('taskId') == task_id), {})
        requests = _rows(graph.get('requests'))
        paragraphs = _rows(graph.get('paragraphs'))
        if graph.get('evidenceStatus') in {None, 'missing', 'unavailable'}:
            finding('evidence_unavailable', taskId=task_id)
        elif graph.get('evidenceStatus') == 'partial':
            finding('evidence_incomplete', taskId=task_id)
        by_call = {row.get('callId'): row for row in requests}
        executions = {row.get('executionId') for row in _rows(graph.get('executions'))}
        if graph.get('evidenceStatus') == 'available' and any(
                row.get('executionId') not in executions or
                (row.get('parentCallId') and row['parentCallId'] not in by_call) for row in requests):
            finding('evidence_incomplete', taskId=task_id)
        by_paragraph = {}
        for row in paragraphs:
            by_paragraph.setdefault((row.get('paragraphRef'), row.get('attempt')), []).append(row)
        linked = 0
        for failed in task.get('failedParagraphs', []):
            match = by_paragraph.get((failed.get('paragraphRef'), task.get('attempt')), []) if failed.get('paragraphRef') else []
            refs = list(dict.fromkeys(ref for row in match for ref in row.get('callRefs', []) if ref in by_call))
            linked += bool(refs)
            if failed.get('validationCode'):
                finding('validation_failed', taskId=task_id, paragraphRef=failed.get('paragraphRef'), page=failed.get('page'),
                        validationCode=failed['validationCode'], callRefs=refs)
        for row in requests:
            for rejected in _rows(row.get('validationFailures')):
                finding('validation_failed', taskId=task_id, attempt=row.get('attempt'), callId=row.get('callId'),
                        paragraphRef=rejected.get('paragraphRef'), validationCode=rejected.get('validationCode'))
            if row.get('requestState') == 'failed' and row.get('errorType') != 'InvalidTranslation':
                finding('provider_rate_limited' if row.get('statusCode') == 429 else 'provider_request_failed',
                        taskId=task_id, callId=row.get('callId'), attempt=row.get('attempt'),
                        statusCode=row.get('statusCode'), errorType=row.get('errorType'), providerCode=row.get('providerCode'))
            if row.get('requestState') == 'interrupted':
                finding('interrupted_requests', taskId=task_id, callId=row.get('callId'), attempt=row.get('attempt'))
        summary = task.get('translationSummary')
        total = (summary if isinstance(summary, dict) else {}).get('failed', len(task.get('failedParagraphs', [])))
        if total:
            finding('paragraph_unresolved', taskId=task_id, totalFailures=total, linkedFailures=linked)
            if linked < total and graph.get('evidenceStatus') == 'available':
                finding('evidence_incomplete', taskId=task_id, totalFailures=total, linkedFailures=linked)
        reused = sum(row.get('outcomeSource') in {'cache', 'checkpoint'} for row in paragraphs)
        if reused: finding('cache_reused', taskId=task_id, cacheReuseCount=reused)
    return {'schemaVersion': CONTRACT['schemaVersion'], 'schemaRevision': CONTRACT['schemaRevision'],
            'findings': findings, 'omittedFindings': omitted}


def analyze_bundle(path):
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        selected = [name for name in ('task.json', 'server.json', 'evidence.json') if name in names]
        if not selected:
            raise ValueError('invalid_diagnostic_bundle')
        if sum(archive.getinfo(name).file_size for name in selected) > 20 * 1024 * 1024:
            raise ValueError('diagnostic_bundle_too_large')
        data = {}
        for name in selected:
            raw = json.loads(archive.read(name))
            if not isinstance(raw, dict) or raw.get('schemaVersion', 1) != 1:
                raise ValueError('unsupported_diagnostic_schema')
            data[name] = safe_fields(raw)
    server = data.get('server.json', {})
    task = data.get('task.json', {})
    tasks = [task] if task.get('taskId') else server.get('tasks', [])
    return analyze(tasks, data.get('evidence.json', server.get('evidence', {})))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Analyze a diagnostic ZIP locally without contacting providers.')
    parser.add_argument('bundle', type=Path)
    parser.add_argument('--text', action='store_true', help='Print registered explanations and suggestions.')
    args = parser.parse_args()
    try:
        result = analyze_bundle(args.bundle)
    except (OSError, ValueError, zipfile.BadZipFile, RecursionError):
        parser.exit(2, 'Unable to analyze bundle: invalid, oversized or unsupported diagnostic data.\n')
    if args.text:
        for row in result['findings']:
            rule = CONTRACT['diagnosisRules'][row['findingCode']]
            print(f"{row.get('taskId', '')}: {rule['title']}")
            print(rule['suggestion'])
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
