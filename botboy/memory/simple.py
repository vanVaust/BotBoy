"""SimpleMemoryEngine — Thread-safe SQLite FTS5 persistent memory.

:memory: databases use a single shared connection + Lock.
File databases use threading.local() connection pool + WAL mode.
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


_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    content   TEXT    NOT NULL,
    timestamp TEXT    NOT NULL,
    metadata  TEXT
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

    def to_dict(self) -> dict:
        return {"id": self.id, "content": self.content,
                "timestamp": self.timestamp, "metadata": self.metadata}


class SimpleMemoryEngine(_SQLiteMixin):
    """FTS5 BM25 ranked full-text search with per-thread connection pool."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self._init_connection_pool(db_path, _SCHEMA)

    def _row_to_memory(self, row) -> Memory:
        meta = None
        if row["metadata"]:
            try:
                meta = json.loads(row["metadata"])
            except Exception:
                meta = {"raw": row["metadata"]}
        return Memory(id=row["id"], content=row["content"],
                      timestamp=row["timestamp"], metadata=meta)

    def store(self, content: str, metadata: Optional[dict] = None) -> int:
        ts = datetime.now(timezone.utc).isoformat()
        meta_json = json.dumps(metadata) if metadata else None
        if self._is_memory:
            with self._shared_lock:
                cur = self._shared_conn.execute(
                    "INSERT INTO memories (content, timestamp, metadata) VALUES (?,?,?)",
                    (content, ts, meta_json))
                self._shared_conn.commit()
                return cur.lastrowid
        else:
            conn = self._get_conn()
            cur = conn.execute(
                "INSERT INTO memories (content, timestamp, metadata) VALUES (?,?,?)",
                (content, ts, meta_json))
            conn.commit()
            return cur.lastrowid

    def store_batch(self, items: List[str]) -> List[int]:
        ts = datetime.now(timezone.utc).isoformat()
        ids = []
        if self._is_memory:
            with self._shared_lock:
                for content in items:
                    cur = self._shared_conn.execute(
                        "INSERT INTO memories (content, timestamp, metadata) VALUES (?,?,NULL)",
                        (content, ts))
                    ids.append(cur.lastrowid)
                self._shared_conn.commit()
        else:
            conn = self._get_conn()
            for content in items:
                cur = conn.execute(
                    "INSERT INTO memories (content, timestamp, metadata) VALUES (?,?,NULL)",
                    (content, ts))
                ids.append(cur.lastrowid)
            conn.commit()
        return ids

    def search(self, query: str, limit: int = 5) -> List[Memory]:
        sql_fts = """SELECT m.id, m.content, m.timestamp, m.metadata
                     FROM memories m JOIN memories_fts fts ON m.id = fts.rowid
                     WHERE memories_fts MATCH ? ORDER BY bm25(memories_fts) LIMIT ?"""
        sql_like = "SELECT id, content, timestamp, metadata FROM memories WHERE content LIKE ? LIMIT ?"
        try:
            rows = self._fetchall(sql_fts, (query, limit))
        except sqlite3.OperationalError:
            rows = self._fetchall(sql_like, (f"%{query}%", limit))
        return [self._row_to_memory(r) for r in rows]

    def get(self, memory_id: int) -> Optional[Memory]:
        row = self._fetchone(
            "SELECT id, content, timestamp, metadata FROM memories WHERE id = ?", (memory_id,))
        return self._row_to_memory(row) if row else None

    def update(self, memory_id: int, content: str) -> bool:
        ts = datetime.now(timezone.utc).isoformat()
        if self._is_memory:
            with self._shared_lock:
                self._shared_conn.execute(
                    "UPDATE memories SET content=?, timestamp=? WHERE id=?", (content, ts, memory_id))
                self._shared_conn.commit()
        else:
            conn = self._get_conn()
            conn.execute("UPDATE memories SET content=?, timestamp=? WHERE id=?",
                         (content, ts, memory_id))
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

    def list_all(self, limit: int = 100, offset: int = 0) -> List[Memory]:
        rows = self._fetchall(
            "SELECT id, content, timestamp, metadata FROM memories ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset))
        return [self._row_to_memory(r) for r in rows]

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
