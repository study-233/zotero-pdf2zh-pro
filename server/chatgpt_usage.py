"""Durable, content-free accounting for this installation's ChatGPT calls."""
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class UsageStore:
    def __init__(self, directory):
        self.path = Path(directory) / 'usage.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS calls (id TEXT PRIMARY KEY, account TEXT NOT NULL, '
                       'started REAL NOT NULL, model TEXT, purpose TEXT, status TEXT, '
                       'input INTEGER, output INTEGER, cached INTEGER, reasoning INTEGER, complete INTEGER DEFAULT 0)')
        if os.name != 'nt':
            self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            with db:
                yield db
        finally:
            db.close()

    def start(self, call_id, account, model, purpose):
        with self.connect() as db:
            db.execute('INSERT INTO calls(id,account,started,model,purpose,status) VALUES(?,?,?,?,?,?)',
                       (call_id, account, time.time(), model, purpose, 'unconfirmed'))

    def finish(self, call_id, status, usage, complete):
        def count(key):
            value = usage.get(key)
            return value if type(value) is int and value >= 0 else None
        with self.connect() as db:
            db.execute('UPDATE calls SET status=?,input=?,output=?,cached=?,reasoning=?,complete=? WHERE id=?',
                       (status, count('prompt_tokens'), count('completion_tokens'), count('cache_hit_tokens'),
                        count('reasoning_tokens'), int(complete and count('prompt_tokens') is not None
                                                     and count('completion_tokens') is not None), call_id))

    def summary(self, account):
        with self.connect() as db:
            row = db.execute('SELECT COUNT(*), SUM(input), SUM(output), SUM(cached), SUM(reasoning), '
                             'SUM(complete), SUM(status="ok"), SUM(status="cancelled"), MIN(started) '
                             'FROM calls WHERE account=?', (account,)).fetchone()
        calls = row[0]
        return {'scope': 'this_installation', 'calls': calls,
                'inputTokens': row[1] if calls else 0, 'outputTokens': row[2] if calls else 0,
                'cachedInputTokens': row[3] if calls else 0, 'reasoningTokens': row[4] if calls else 0,
                'completeCalls': row[5] or 0, 'unknownUsageCalls': calls - (row[5] or 0),
                'succeeded': row[6] or 0, 'cancelled': row[7] or 0, 'since': row[8],
                'updatedAt': time.time(), 'quotaPercent': None,
                'quotaAvailability': 'not_available', 'quotaSource': None}
