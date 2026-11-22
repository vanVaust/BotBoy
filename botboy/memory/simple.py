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

TRUST_LEVELS = ("trusted", "untrusted")
_MAX_SOURCE_CHARS = 64
_COLUMNS = "id, content, timestamp, metadata, trust, source"
_M_COLUMNS = "m.id, m.content, m.timestamp, m.metadata, m.trust, m.source"
_PROVENANCE_COLUMNS = (
    ("trust", "TEXT NOT NULL DEFAULT 'untrusted' CHECK (trust IN ('trusted', 'untrusted'))"),
    ("source", "TEXT NOT NULL DEFAULT ''"),
)


def _check_provenance(trust: str, source: str) -> tuple[str, str]:
    if trust not in TRUST_LEVELS:
        raise ValueError(f"trust must be one of {TRUST_LEVELS}, got {trust!r}")
    if not isinstance(source, str) or len(source) > _MAX_SOURCE_CHARS:
        raise ValueError(f"source must be a string of at most {_MAX_SOURCE_CHARS} characters")
    return trust, source


_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    content   TEXT    NOT NULL,
    timestamp TEXT    NOT NULL,
    metadata  TEXT,
    trust     TEXT    NOT NULL DEFAULT 'untrusted' CHECK (trust IN ('trusted', 'untrusted')),
    source    TEXT    NOT NULL DEFAULT ''
);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content,
    content='memories',
    content_rowid='id',
    tokenize='porter ascii'
);
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content) VALUES ('delete', old.id, old.content);
    INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;
"""


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
        self._migrate_provenance()

    def _migrate_provenance(self) -> None:
        """Add the provenance columns to databases created before they existed.

        ``CREATE TABLE IF NOT EXISTS`` leaves old tables untouched, so the
        columns are added here; existing rows get the ``untrusted`` default.
        """
        def migrate(conn: sqlite3.Connection) -> None:
            for name, ddl in _PROVENANCE_COLUMNS:
                present = {row[1] for row in conn.execute("PRAGMA table_info(memories)")}
                if name in present:
                    continue
                try:
                    conn.execute(f"ALTER TABLE memories ADD COLUMN {name} {ddl}")
                except sqlite3.OperationalError as exc:
                    # Another process may have migrated between check and ALTER.
                    if "duplicate column" not in str(exc).lower():
                        raise
            conn.commit()

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
        sql = "INSERT INTO memories (content, timestamp, metadata, trust, source) VALUES (?,?,?,?,?)"
        if self._is_memory:
            with self._shared_lock:
                cur = self._shared_conn.execute(sql, (content, ts, meta_json, trust, source))
                self._shared_conn.commit()
                return cur.lastrowid
        conn = self._get_conn()
        cur = conn.execute(sql, (content, ts, meta_json, trust, source))
        conn.commit()
        return cur.lastrowid

    def store_batch(self, items: List[str], *, trust: str = "untrusted", source: str = "") -> List[int]:
        trust, source = _check_provenance(trust, source)
        ts = datetime.now(timezone.utc).isoformat()
        sql = "INSERT INTO memories (content, timestamp, metadata, trust, source) VALUES (?,?,NULL,?,?)"
        ids = []
        if self._is_memory:
            with self._shared_lock:
                for content in items:
                    ids.append(self._shared_conn.execute(sql, (content, ts, trust, source)).lastrowid)
                self._shared_conn.commit()
        else:
            conn = self._get_conn()
            for content in items:
                ids.append(conn.execute(sql, (content, ts, trust, source)).lastrowid)
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
        scope, params = self._scope_filter(principal=principal, org_id=org_id, alias="m")
        sql_fts = f"""SELECT {_M_COLUMNS}
                     FROM memories m JOIN memories_fts fts ON m.id = fts.rowid
                     WHERE memories_fts MATCH ? {scope} ORDER BY bm25(memories_fts) LIMIT ?"""
        sql_like = f"SELECT {_M_COLUMNS} FROM memories m WHERE content LIKE ? {scope} ORDER BY m.id DESC LIMIT ?"
        try:
            rows = self._fetchall(sql_fts, [query, *params, limit])
        except sqlite3.OperationalError:
            rows = self._fetchall(sql_like, [f"%{query}%", *params, limit])
        return [self._row_to_memory(r) for r in rows]

    def get(self, memory_id: int) -> Optional[Memory]:
        row = self._fetchone(
            f"SELECT {_COLUMNS} FROM memories WHERE id = ?", (memory_id,))
        return self._row_to_memory(row) if row else None

    def update(self, memory_id: int, content: str, *, trust: str = "untrusted", source: str = "update") -> bool:
        """Replace content; the provenance label is replaced with it."""
        trust, source = _check_provenance(trust, source)
        ts = datetime.now(timezone.utc).isoformat()
        sql = "UPDATE memories SET content=?, timestamp=?, trust=?, source=? WHERE id=?"
        if self._is_memory:
            with self._shared_lock:
                self._shared_conn.execute(sql, (content, ts, trust, source, memory_id))
                self._shared_conn.commit()
        else:
            conn = self._get_conn()
            conn.execute(sql, (content, ts, trust, source, memory_id))
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
