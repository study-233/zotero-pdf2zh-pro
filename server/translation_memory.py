"""Long-lived reading memory, independent of disposable task checkpoints."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
from contextlib import closing
from pathlib import Path


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).replace("\u00ad", "")
    return " ".join(text.split())


def document_fingerprint(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class TranslationMemory:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE,
                    source_filename TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS paragraphs (
                    id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL REFERENCES documents(id),
                    target_lang TEXT NOT NULL, page INTEGER NOT NULL, paragraph_id TEXT NOT NULL,
                    source TEXT NOT NULL, translation TEXT NOT NULL,
                    normalized_source TEXT NOT NULL, normalized_translation TEXT NOT NULL,
                    source_hash TEXT NOT NULL, formatting_incomplete INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(document_id, target_lang, page, paragraph_id, source_hash)
                );
                CREATE INDEX IF NOT EXISTS idx_memory_page ON paragraphs(document_id, target_lang, page);
                CREATE INDEX IF NOT EXISTS idx_memory_source ON paragraphs(document_id, source_hash);
            """)
            if "formatting_incomplete" not in {row[1] for row in db.execute("PRAGMA table_info(paragraphs)")}:
                db.execute("ALTER TABLE paragraphs ADD COLUMN formatting_incomplete INTEGER NOT NULL DEFAULT 0")

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def upsert(self, fingerprint, entries, *, target_lang="zh-CN", filename=""):
        rows = []
        for entry in entries:
            source = entry.get("source") or ""
            translation = entry.get("readingTranslation", entry.get("translation")) or ""
            if (entry.get("status") != "succeeded" or not source.strip() or not translation.strip()
                    or entry.get("quality", {}).get("status") == "failed"):
                continue
            # Legacy checkpoints may lack the composition map. Keep their prose
            # reusable (zero provider calls), but label unavailable layout text.
            incomplete = False
            if "readingTranslation" not in entry:
                translation = re.sub(r"</?style(?:\s[^>]*)?>", "", translation)
                translation, replacements = re.subn(r"\{/?[vti]\d+\}|<[/]?[vti]\d+>", "⟦原排版内容⟧", translation)
                incomplete = replacements > 0
            normalized = normalize_text(source)
            if not normalized or not normalize_text(translation):
                continue
            rows.append((target_lang.lower().replace("_", "-"), entry["page"], str(entry.get("paragraphId") or ""),
                         source, translation, normalized, normalize_text(translation),
                         hashlib.sha256(normalized.encode()).hexdigest(), int(incomplete)))
        with closing(self.connect()) as db, db:
            db.execute("INSERT INTO documents(fingerprint, source_filename) VALUES (?, ?) "
                       "ON CONFLICT(fingerprint) DO UPDATE SET updated_at=CURRENT_TIMESTAMP",
                       (fingerprint, filename))
            document_id = db.execute("SELECT id FROM documents WHERE fingerprint=?", (fingerprint,)).fetchone()[0]
            db.executemany("""INSERT INTO paragraphs(document_id, target_lang, page, paragraph_id,
                source, translation, normalized_source, normalized_translation, source_hash, formatting_incomplete)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(document_id, target_lang, page, paragraph_id, source_hash) DO UPDATE SET
                translation=excluded.translation, normalized_translation=excluded.normalized_translation,
                formatting_incomplete=excluded.formatting_incomplete
            """, [(document_id, *row) for row in rows])
        return len(rows)

    def lookup(self, fingerprint, text, *, page=None, side="source", target_lang=None):
        selected = normalize_text(text)
        if not selected:
            return {"status": "ok", "matched": False}
        column = "normalized_translation" if side == "translation" else "normalized_source"
        where = "document_id=(SELECT id FROM documents WHERE fingerprint=?)"
        args = [fingerprint]
        if target_lang:
            where += " AND target_lang=?"
            args.append(target_lang.lower().replace("_", "-"))
        with closing(self.connect()) as db:
            exact = f"{column}=?"
            exact_args = [selected]
            if side == "source":
                exact += " AND source_hash=?"
                exact_args.append(hashlib.sha256(selected.encode()).hexdigest())
            row = db.execute(f"SELECT * FROM paragraphs WHERE {where} AND {exact} "
                             "ORDER BY (page=?) DESC, id DESC LIMIT 1",
                             (*args, *exact_args, page)).fetchone()
            # Page-local containment first. instr treats %, _ and quotes literally.
            if row is None and page is not None:
                row = db.execute(f"SELECT * FROM paragraphs WHERE {where} AND page=? AND instr({column}, ?) > 0 "
                                 f"ORDER BY length({column}), id DESC LIMIT 1", (*args, page, selected)).fetchone()
            if row is None:
                row = db.execute(f"SELECT * FROM paragraphs WHERE {where} AND instr({column}, ?) > 0 "
                                 f"ORDER BY length({column}), id DESC LIMIT 1", (*args, selected)).fetchone()
        if row is None:
            return {"status": "ok", "matched": False}
        return {"status": "ok", "matched": True, "matchType": "exact" if row[column] == selected else "contained",
                "selected": text, "source": row["source"], "translation": row["translation"],
                "page": row["page"], "paragraphId": row["paragraph_id"], "targetLang": row["target_lang"],
                "fromTranslationMemory": True, "formattingIncomplete": bool(row["formatting_incomplete"])}


def sync_recovery(memory_path, payload):
    """Read only the checkpoint; never alter its fingerprint, retries or lifetime."""
    if not payload.get("input_path"):
        return 0
    input_path = Path(payload["input_path"])
    checkpoint = input_path.parent / "paragraph-recovery.sqlite3"
    if not checkpoint.is_file() or not input_path.is_file():
        return 0
    with closing(sqlite3.connect(checkpoint.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        entries = [json.loads(row[0]) for row in db.execute("SELECT payload FROM paragraphs")]
    return TranslationMemory(memory_path).upsert(document_fingerprint(input_path), entries,
        target_lang=payload.get("target_lang", "zh-CN"), filename=input_path.name)
