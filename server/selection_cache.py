"""Persistent selection results, separate from document translation memory."""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path


class SelectionCache:
    MAX_ROWS = 20000

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.execute('''CREATE TABLE IF NOT EXISTS selection_results (
                key TEXT PRIMARY KEY, kind TEXT NOT NULL, result TEXT NOT NULL,
                accessed REAL NOT NULL, created REAL NOT NULL)''')
            db.execute('CREATE INDEX IF NOT EXISTS selection_lru ON selection_results(kind, accessed)')

    def connect(self):
        return sqlite3.connect(self.path, timeout=5)

    def get(self, key):
        with closing(self.connect()) as db, db:
            row = db.execute('SELECT result FROM selection_results WHERE key=?', (key,)).fetchone()
            if row is None:
                return None
            db.execute('UPDATE selection_results SET accessed=? WHERE key=?', (time.time(), key))
            return json.loads(row[0])

    def put(self, key, kind, result):
        now = time.time()
        with closing(self.connect()) as db, db:
            db.execute('''INSERT INTO selection_results VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET kind=excluded.kind, result=excluded.result,
                accessed=excluded.accessed, created=excluded.created''',
                (key, kind, json.dumps(result, ensure_ascii=False), now, now))
            db.execute('''DELETE FROM selection_results WHERE key IN (
                SELECT key FROM selection_results WHERE kind != 'dictionary'
                ORDER BY accessed DESC, key LIMIT -1 OFFSET ?)''', (self.MAX_ROWS,))

    def clear_translations(self):
        with closing(self.connect()) as db, db:
            db.execute("DELETE FROM selection_results WHERE kind != 'dictionary'")


def dictionary_entry(value, selected):
    """Accept a bounded, plain-text AI supplement; never generated phonetics/HTML."""
    def text(value, limit=4000):
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise ValueError('invalid dictionary text')
        return value.strip()

    if not isinstance(value, dict) or not isinstance(value.get('senses'), list) or not 1 <= len(value['senses']) <= 12:
        raise ValueError('invalid dictionary entry')
    senses = []
    for sense in value['senses']:
        if not isinstance(sense, dict):
            raise ValueError('invalid sense')
        result = {'chinese': text(sense.get('chinese')), 'examples': []}
        for field in ('english', 'pos'):
            if sense.get(field):
                result[field] = text(sense[field])
        examples = sense.get('examples', [])
        if not isinstance(examples, list) or len(examples) > 2:
            raise ValueError('invalid examples')
        for example in examples:
            if not isinstance(example, dict):
                raise ValueError('invalid example')
            result['examples'].append({'english': text(example.get('english')), 'chinese': text(example.get('chinese'))})
        senses.append(result)
    result = {'headword': selected, 'senses': senses, 'aiGenerated': True}
    if value.get('usage'):
        result['usage'] = text(value['usage'])
    return result


def context_meaning(value):
    """Validate structured context without interpreting provider markup."""
    if not isinstance(value, dict):
        raise ValueError('invalid context meaning')
    result = {}
    for field in ('meaning', 'explanation', 'pos'):
        if field == 'pos' and field not in value:
            continue
        text = value.get(field)
        if not isinstance(text, str) or not text.strip() or len(text) > (80 if field == 'pos' else 4000):
            raise ValueError('invalid context meaning')
        result[field] = text.strip()
    return result
