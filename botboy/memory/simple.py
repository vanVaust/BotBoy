"""SimpleMemoryEngine — Thread-safe SQLite FTS5 persistent memory.

:memory: databases use a single shared connection + Lock.
File databases use threading.local() connection pool + WAL mode.

Provenance: every row carries ``trust`` (``trusted``/``untrusted``) and a
free-form ``source``. Only the write path decides trust, from where the text
came from, never from caller-supplied metadata. Unknown provenance —
including every row that predates the column — is ``untrusted``, and any
content change resets the label, so an edit cannot inherit trust.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from botboy.db_mixin import _SQLiteMixin
from botboy.memory.textnorm import NORMALIZATION_VERSION, build_match_query, normalize_for_index

TRUST_LEVELS = ("trusted", "untrusted")
_MAX_SOURCE_CHARS = 64
_COLUMNS = "id, content, timestamp, metadata, trust, source"
_M_COLUMNS = "m.id, m.content, m.timestamp, m.metadata, m.trust, m.source"
_ADDED_COLUMNS = (
    ("trust", "TEXT NOT NULL DEFAULT 'untrusted' CHECK (trust IN ('trusted', 'untrusted'))"),
    ("source", "TEXT NOT NULL DEFAULT ''"),
    ("search_text", "TEXT"),
)


def _check_provenance(trust: str, source: str) -> tuple[str, str]:
    if trust not in TRUST_LEVELS:
        raise ValueError(f"trust must be one of {TRUST_LEVELS}, got {trust!r}")
    if not isinstance(source, str) or len(source) > _MAX_SOURCE_CHARS:
        raise ValueError(f"source must be a string of at most {_MAX_SOURCE_CHARS} characters")
    return trust, source


_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    content     TEXT    NOT NULL,
    timestamp   TEXT    NOT NULL,
    metadata    TEXT,
    trust       TEXT    NOT NULL DEFAULT 'untrusted' CHECK (trust IN ('trusted', 'untrusted')),
    source      TEXT    NOT NULL DEFAULT '',
    search_text TEXT
);
CREATE TABLE IF NOT EXISTS memory_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# The index covers the normalised shadow column (see botboy.memory.textnorm).
# Created after migration so the triggers never reference a missing column.
_FTS_SCHEMA = (
    """CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts2 USING fts5(
        search_text, content='memories', content_rowid='id',
        tokenize='porter unicode61 remove_diacritics 2')""",
    """CREATE TRIGGER IF NOT EXISTS memories_fts2_ai AFTER INSERT ON memories BEGIN
        INSERT INTO memories_fts2(rowid, search_text) VALUES (new.id, new.search_text);
    END""",
    """CREATE TRIGGER IF NOT EXISTS memories_fts2_au AFTER UPDATE OF search_text ON memories BEGIN
        INSERT INTO memories_fts2(memories_fts2, rowid, search_text) VALUES ('delete', old.id, old.search_text);
        INSERT INTO memories_fts2(rowid, search_text) VALUES (new.id, new.search_text);
    END""",
    """CREATE TRIGGER IF NOT EXISTS memories_fts2_ad AFTER DELETE ON memories BEGIN
        INSERT INTO memories_fts2(memories_fts2, rowid, search_text) VALUES ('delete', old.id, old.search_text);
    END""",
)
_LEGACY_FTS = (
    "DROP TRIGGER IF EXISTS memories_ai",
    "DROP TRIGGER IF EXISTS memories_au",
    "DROP TRIGGER IF EXISTS memories_ad",
    "DROP TABLE IF EXISTS memories_fts",
)
_BACKFILL_BATCH = 500


@dataclass
class Memory:
    id: int
    content: str
    timestamp: str
    metadata: Optional[Dict] = None
    trust: str = "untrusted"
    source: str = ""

    def to_dict(self) -> dict:
        return {"id": self.id, "content": self.content,
                "timestamp": self.timestamp, "metadata": self.metadata,
                "trust": self.trust, "source": self.source}


class SimpleMemoryEngine(_SQLiteMixin):
    """FTS5 BM25 ranked full-text search with per-thread connection pool."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self._init_connection_pool(db_path, _SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Bring any older database to the current layout, idempotently.

        * adds ``trust``/``source`` (old rows: untrusted) and ``search_text``;
        * replaces the legacy ``porter ascii`` index over raw content with the
          normalised ``memories_fts2`` index;
        * when the index is missing, a row lacks ``search_text`` (written by an
          older BotBoy) or the normalisation version changed, recomputes every
          ``search_text`` and rebuilds the index from scratch.

        Runs under ``BEGIN IMMEDIATE`` so concurrent openers serialise and
        each re-checks the state inside the lock.
        """
        def migrate(conn: sqlite3.Connection) -> None:
            previous = conn.isolation_level
            conn.isolation_level = None
            try:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    present = {row[1] for row in conn.execute("PRAGMA table_info(memories)")}
                    for name, ddl in _ADDED_COLUMNS:
                        if name not in present:
                            conn.execute(f"ALTER TABLE memories ADD COLUMN {name} {ddl}")
                    for statement in _LEGACY_FTS:
                        conn.execute(statement)
                    row = conn.execute("SELECT value FROM memory_meta WHERE key='search_norm_version'").fetchone()
                    indexed = conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memories_fts2'").fetchone()
                    unnormalised = conn.execute(
                        "SELECT 1 FROM memories WHERE search_text IS NULL LIMIT 1").fetchone()
                    if row is None or row[0] != NORMALIZATION_VERSION or not indexed or unnormalised:
                        # External-content FTS5 must never be told to delete a
                        # row it does not hold (that corrupts the index), so
                        # recompute with the triggers absent, then rebuild.
                        for name in ("memories_fts2_ai", "memories_fts2_au", "memories_fts2_ad"):
                            conn.execute(f"DROP TRIGGER IF EXISTS {name}")
                        conn.execute("DROP TABLE IF EXISTS memories_fts2")
                        last_id = 0
                        while True:
                            batch = conn.execute("SELECT id, content FROM memories WHERE id > ? ORDER BY id LIMIT ?",
                                                 (last_id, _BACKFILL_BATCH)).fetchall()
                            if not batch:
                                break
                            conn.executemany("UPDATE memories SET search_text=? WHERE id=?",
                                             [(normalize_for_index(content), rid) for rid, content in batch])
                            last_id = batch[-1][0]
                        for statement in _FTS_SCHEMA:
                            conn.execute(statement)
                        conn.execute("INSERT INTO memories_fts2(memories_fts2) VALUES ('rebuild')")
                    conn.execute("INSERT OR REPLACE INTO memory_meta(key, value) VALUES ('search_norm_version', ?)",
                                 (NORMALIZATION_VERSION,))
                    conn.execute("COMMIT")
                except BaseException:
                    conn.execute("ROLLBACK")
                    raise
            finally:
                conn.isolation_level = previous

        if self._is_memory:
            with self._shared_lock:
                migrate(self._shared_conn)
        else:
            conn = sqlite3.connect(self.db_path, timeout=30)
            try:
                migrate(conn)
            finally:
                conn.close()

    def _row_to_memory(self, row) -> Memory:
        meta = None
        if row["metadata"]:
            try:
                meta = json.loads(row["metadata"])
            except Exception:
                meta = {"raw": row["metadata"]}
        keys = row.keys()
        trust = row["trust"] if "trust" in keys and row["trust"] in TRUST_LEVELS else "untrusted"
        return Memory(id=row["id"], content=row["content"],
                      timestamp=row["timestamp"], metadata=meta, trust=trust,
                      source=(row["source"] or "") if "source" in keys else "")

    def store(self, content: str, metadata: Optional[dict] = None, *,
              trust: str = "untrusted", source: str = "") -> int:
        trust, source = _check_provenance(trust, source)
        ts = datetime.now(timezone.utc).isoformat()
        meta_json = json.dumps(metadata) if metadata else None
        sql = ("INSERT INTO memories (content, timestamp, metadata, trust, source, search_text)"
               " VALUES (?,?,?,?,?,?)")
        params = (content, ts, meta_json, trust, source, normalize_for_index(content))
        if self._is_memory:
            with self._shared_lock:
                cur = self._shared_conn.execute(sql, params)
                self._shared_conn.commit()
                return cur.lastrowid
        conn = self._get_conn()
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid

    def store_batch(self, items: List[str], *, trust: str = "untrusted", source: str = "") -> List[int]:
        trust, source = _check_provenance(trust, source)
        ts = datetime.now(timezone.utc).isoformat()
        sql = ("INSERT INTO memories (content, timestamp, metadata, trust, source, search_text)"
               " VALUES (?,?,NULL,?,?,?)")
        ids = []
        if self._is_memory:
            with self._shared_lock:
                for content in items:
                    ids.append(self._shared_conn.execute(
                        sql, (content, ts, trust, source, normalize_for_index(content))).lastrowid)
                self._shared_conn.commit()
        else:
            conn = self._get_conn()
            for content in items:
                ids.append(conn.execute(sql, (content, ts, trust, source, normalize_for_index(content))).lastrowid)
            conn.commit()
        return ids

    @staticmethod
    def _scope_filter(*, principal: Optional[str], org_id: Optional[str], alias: str = "") -> tuple[str, list[str]]:
        column = f"{alias}.metadata" if alias else "metadata"
        # Malformed legacy metadata cannot acquire a tenant merely by parsing.
        document = f"CASE WHEN json_valid({column}) THEN {column} ELSE '{{}}' END"
        conditions, params = [], []
        for key, value in (("org_id", org_id), ("principal_id", principal)):
            if value is not None:
                conditions.append(f"json_extract({document}, '$._botboy_security.{key}') = ?")
                params.append(value)
        return (" AND " + " AND ".join(conditions)) if conditions else "", params

    def search(self, query: str, limit: int = 5, *, principal: Optional[str] = None, org_id: Optional[str] = None) -> List[Memory]:
        """BM25-ranked search over the normalised index; LIKE on raw text as fallback.

        The query is normalised exactly like stored text and compiled to a
        quoted MATCH expression, so FTS syntax in user input cannot error or
        widen the query; the substring fallback covers inputs with no
        indexable term (punctuation only).
        """
        scope, params = self._scope_filter(principal=principal, org_id=org_id, alias="m")
        sql_fts = f"""SELECT {_M_COLUMNS}
                     FROM memories m JOIN memories_fts2 fts ON m.id = fts.rowid
                     WHERE memories_fts2 MATCH ? {scope} ORDER BY bm25(memories_fts2) LIMIT ?"""
        sql_like = f"SELECT {_M_COLUMNS} FROM memories m WHERE content LIKE ? ESCAPE '\\' {scope} ORDER BY m.id DESC LIMIT ?"
        match = build_match_query(query)
        rows = None
        if match is not None:
            try:
                rows = self._fetchall(sql_fts, [match, *params, limit])
            except sqlite3.OperationalError:
                rows = None
        if rows is None:
            needle = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            rows = self._fetchall(sql_like, [f"%{needle}%", *params, limit])
        return [self._row_to_memory(r) for r in rows]

    def get(self, memory_id: int) -> Optional[Memory]:
        row = self._fetchone(
            f"SELECT {_COLUMNS} FROM memories WHERE id = ?", (memory_id,))
        return self._row_to_memory(row) if row else None

    def update(self, memory_id: int, content: str, *, trust: str = "untrusted", source: str = "update") -> bool:
        """Replace content; the provenance label is replaced with it."""
        trust, source = _check_provenance(trust, source)
        ts = datetime.now(timezone.utc).isoformat()
        sql = "UPDATE memories SET content=?, timestamp=?, trust=?, source=?, search_text=? WHERE id=?"
        params = (content, ts, trust, source, normalize_for_index(content), memory_id)
        if self._is_memory:
            with self._shared_lock:
                self._shared_conn.execute(sql, params)
                self._shared_conn.commit()
        else:
            conn = self._get_conn()
            conn.execute(sql, params)
            conn.commit()
        return True

    def delete(self, memory_id: int) -> bool:
        if self._is_memory:
            with self._shared_lock:
                self._shared_conn.execute("DELETE FROM memories WHERE id=?", (memory_id,))
                self._shared_conn.commit()
        else:
            conn = self._get_conn()
            conn.execute("DELETE FROM memories WHERE id=?", (memory_id,))
            conn.commit()
        return True

    def list_all(self, limit: int = 100, offset: int = 0, *, principal: Optional[str] = None, org_id: Optional[str] = None) -> List[Memory]:
        scope, params = self._scope_filter(principal=principal, org_id=org_id)
        rows = self._fetchall(
            f"SELECT {_COLUMNS} FROM memories WHERE 1=1 {scope} ORDER BY id DESC LIMIT ? OFFSET ?",
            [*params, limit, offset])
        return [self._row_to_memory(r) for r in rows]

    def count_scoped(self, *, principal: Optional[str] = None, org_id: Optional[str] = None) -> int:
        scope, params = self._scope_filter(principal=principal, org_id=org_id)
        row = self._fetchone(f"SELECT COUNT(*) AS total FROM memories WHERE 1=1 {scope}", params)
        return int(row["total"])

    def clear_all(self) -> int:
        if self._is_memory:
            with self._shared_lock:
                cur = self._shared_conn.execute("DELETE FROM memories")
                self._shared_conn.commit()
                return cur.rowcount
        conn = self._get_conn()
        cur = conn.execute("DELETE FROM memories")
        conn.commit()
        return cur.rowcount

    def get_stats(self) -> dict:
        row = self._fetchone(
            "SELECT COUNT(*) as total, MIN(timestamp) as oldest, MAX(timestamp) as newest FROM memories")
        db_size = 0
        if self.db_path != ":memory:":
            p = Path(self.db_path)
            if p.exists():
                db_size = p.stat().st_size
        return {"total": row["total"], "oldest": row["oldest"],
                "newest": row["newest"], "db_size_bytes": db_size}

    def close(self) -> None:
        super().close()
