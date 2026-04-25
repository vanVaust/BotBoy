"""Reflection memory archive for BotBoy Welle 19."""
from __future__ import annotations

import json
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from botboy.db_mixin import _SQLiteMixin

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reflection_entries (
    entry_id           TEXT PRIMARY KEY,
    task_id            TEXT NOT NULL DEFAULT '',
    trace_id           TEXT NOT NULL DEFAULT '',
    outcome            TEXT NOT NULL DEFAULT 'unknown',
    summary            TEXT NOT NULL DEFAULT '',
    reflection         TEXT NOT NULL DEFAULT '',
    author             TEXT NOT NULL DEFAULT '',
    source             TEXT NOT NULL DEFAULT '',
    tags_json          TEXT NOT NULL DEFAULT '[]',
    metadata_json      TEXT NOT NULL DEFAULT '{}',
    task_snapshot_json TEXT NOT NULL DEFAULT '{}',
    trace_snapshot_json TEXT NOT NULL DEFAULT '{}',
    created_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reflection_entries_task_id ON reflection_entries(task_id);
CREATE INDEX IF NOT EXISTS idx_reflection_entries_trace_id ON reflection_entries(trace_id);
CREATE INDEX IF NOT EXISTS idx_reflection_entries_outcome ON reflection_entries(outcome);
CREATE INDEX IF NOT EXISTS idx_reflection_entries_created_at ON reflection_entries(created_at);
"""

_TASK_SNAPSHOT_KEYS = (
    "task_id",
    "id",
    "title",
    "summary",
    "status",
    "worker_id",
    "owner",
    "run_id",
    "trace_id",
    "request_id",
    "result",
    "outcome",
)
_TRACE_SNAPSHOT_KEYS = (
    "trace_id",
    "run_id",
    "task_id",
    "principal",
    "source",
    "request_id",
    "status",
    "command",
    "summary",
)


@dataclass(frozen=True)
class ReflectionMemoryEntry:
    entry_id: str
    task_id: str
    trace_id: str
    outcome: str
    summary: str
    reflection: str
    author: str
    source: str
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    task_snapshot: Dict[str, Any] = field(default_factory=dict)
    trace_snapshot: Dict[str, Any] = field(default_factory=dict)
    created_at: str = ""

    def to_dict(self) -> dict:
        return {
            "entry_id": self.entry_id,
            "task_id": self.task_id,
            "trace_id": self.trace_id,
            "outcome": self.outcome,
            "summary": self.summary,
            "reflection": self.reflection,
            "author": self.author,
            "source": self.source,
            "tags": list(self.tags),
            "metadata": dict(self.metadata),
            "task_snapshot": dict(self.task_snapshot),
            "trace_snapshot": dict(self.trace_snapshot),
            "created_at": self.created_at,
        }


class ReflectionMemoryArchive(_SQLiteMixin):
    """SQLite-backed archive for task, trace, and outcome reflections."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self._init_connection_pool(db_path, _SCHEMA)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}-{secrets.token_hex(8)}"

    @staticmethod
    def _json_dumps(value: Any) -> str:
        return json.dumps(value, sort_keys=True, ensure_ascii=True, default=str)

    @staticmethod
    def _coerce_mapping(obj: Any) -> Dict[str, Any]:
        if obj is None:
            return {}
        if isinstance(obj, Mapping):
            return dict(obj)
        if hasattr(obj, "__dict__"):
            return dict(vars(obj))
        return {}

    @classmethod
    def _snapshot(cls, obj: Any, keys: Iterable[str]) -> Dict[str, Any]:
        data = cls._coerce_mapping(obj)
        snapshot: Dict[str, Any] = {}
        for key in keys:
            if key in data and data[key] not in (None, ""):
                snapshot[key] = data[key]
        return snapshot

    @staticmethod
    def _extract_text(obj: Any, keys: Iterable[str], default: str = "") -> str:
        data = ReflectionMemoryArchive._coerce_mapping(obj)
        for key in keys:
            value = data.get(key)
            if value not in (None, ""):
                return str(value)
        return default

    @staticmethod
    def _normalize_tags(tags: Optional[Iterable[str]]) -> List[str]:
        normalized: List[str] = []
        for tag in tags or ():
            text = str(tag).strip()
            if text and text not in normalized:
                normalized.append(text)
        return normalized

    @staticmethod
    def _merge_metadata(*payloads: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
        merged: Dict[str, Any] = {}
        for payload in payloads:
            if not payload:
                continue
            for key, value in payload.items():
                if value is not None:
                    merged[key] = value
        return merged

    def archive(
        self,
        *,
        task_id: str = "",
        trace_id: str = "",
        outcome: str = "unknown",
        summary: str = "",
        reflection: str = "",
        author: str = "",
        source: str = "",
        tags: Optional[Iterable[str]] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        task_snapshot: Optional[Mapping[str, Any]] = None,
        trace_snapshot: Optional[Mapping[str, Any]] = None,
    ) -> ReflectionMemoryEntry:
        entry_id = self._new_id("ref")
        created_at = self._now()
        normalized_tags = self._normalize_tags(tags)
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO reflection_entries (
                entry_id, task_id, trace_id, outcome, summary, reflection,
                author, source, tags_json, metadata_json,
                task_snapshot_json, trace_snapshot_json, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                entry_id,
                task_id or "",
                trace_id or "",
                (outcome or "unknown").strip() or "unknown",
                summary or "",
                reflection or "",
                author or "",
                source or "",
                self._json_dumps(normalized_tags),
                self._json_dumps(dict(metadata or {})),
                self._json_dumps(dict(task_snapshot or {})),
                self._json_dumps(dict(trace_snapshot or {})),
                created_at,
            ),
        )
        conn.commit()
        return ReflectionMemoryEntry(
            entry_id=entry_id,
            task_id=task_id or "",
            trace_id=trace_id or "",
            outcome=(outcome or "unknown").strip() or "unknown",
            summary=summary or "",
            reflection=reflection or "",
            author=author or "",
            source=source or "",
            tags=normalized_tags,
            metadata=dict(metadata or {}),
            task_snapshot=dict(task_snapshot or {}),
            trace_snapshot=dict(trace_snapshot or {}),
            created_at=created_at,
        )

    def archive_context(
        self,
        *,
        task: Any = None,
        trace: Any = None,
        outcome: str = "unknown",
        summary: str = "",
        reflection: str = "",
        tags: Optional[Iterable[str]] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        author: str = "",
        source: str = "",
    ) -> ReflectionMemoryEntry:
        task_snapshot = self._snapshot(task, _TASK_SNAPSHOT_KEYS)
        trace_snapshot = self._snapshot(trace, _TRACE_SNAPSHOT_KEYS)
        task_id = self._extract_text(task, ("task_id", "id"), task_snapshot.get("task_id", ""))
        trace_id = self._extract_text(trace, ("trace_id", "run_id"), trace_snapshot.get("trace_id", ""))
        inferred_outcome = outcome if outcome != "unknown" else self._extract_text(
            task,
            ("outcome", "status", "result"),
            "unknown",
        )
        inferred_summary = summary or self._extract_text(task, ("summary", "title"), "")
        inferred_reflection = reflection or self._extract_text(task, ("reflection", "notes"), "")
        merged_metadata = self._merge_metadata(
            metadata,
            self._coerce_mapping(getattr(task, "metadata", None)),
            self._coerce_mapping(getattr(trace, "metadata", None)),
        )
        if task_snapshot:
            merged_metadata.setdefault("task_snapshot", dict(task_snapshot))
        if trace_snapshot:
            merged_metadata.setdefault("trace_snapshot", dict(trace_snapshot))
        return self.archive(
            task_id=task_id,
            trace_id=trace_id,
            outcome=inferred_outcome,
            summary=inferred_summary,
            reflection=inferred_reflection,
            author=author or self._extract_text(task, ("author", "principal"), ""),
            source=source or self._extract_text(trace, ("source", "component"), ""),
            tags=tags,
            metadata=merged_metadata,
            task_snapshot=task_snapshot,
            trace_snapshot=trace_snapshot,
        )

    def list_entries(
        self,
        *,
        task_id: str = "",
        trace_id: str = "",
        outcome: str = "",
        tag: str = "",
        limit: int = 20,
        offset: int = 0,
    ) -> List[ReflectionMemoryEntry]:
        clauses: List[str] = []
        params: List[Any] = []
        if task_id:
            clauses.append("task_id = ?")
            params.append(task_id)
        if trace_id:
            clauses.append("trace_id = ?")
            params.append(trace_id)
        if outcome:
            clauses.append("outcome = ?")
            params.append(outcome)
        if tag:
            clauses.append("tags_json LIKE ?")
            params.append(f'%"{tag}"%')
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._fetchall(
            f"""
            SELECT * FROM reflection_entries
            {where}
            ORDER BY created_at DESC, entry_id DESC
            LIMIT ? OFFSET ?
            """,
            params + [limit, offset],
        )
        return [self._row_to_entry(row) for row in rows]

    def latest_for_task(self, task_id: str) -> Optional[ReflectionMemoryEntry]:
        rows = self.list_entries(task_id=task_id, limit=1)
        return rows[0] if rows else None

    def stats(self) -> dict:
        conn = self._get_conn()
        total = conn.execute("SELECT COUNT(*) FROM reflection_entries").fetchone()[0]
        by_outcome_rows = conn.execute(
            "SELECT outcome, COUNT(*) AS cnt FROM reflection_entries GROUP BY outcome ORDER BY cnt DESC, outcome ASC"
        ).fetchall()
        by_tag_rows = conn.execute(
            """
            SELECT value AS tag, COUNT(*) AS cnt
            FROM reflection_entries, json_each(reflection_entries.tags_json)
            GROUP BY value
            ORDER BY cnt DESC, tag ASC
            LIMIT 10
            """
        ).fetchall()
        latest_row = conn.execute(
            "SELECT * FROM reflection_entries ORDER BY created_at DESC, entry_id DESC LIMIT 1"
        ).fetchone()
        latest = self._row_to_entry(latest_row).to_dict() if latest_row else None
        return {
            "available": True,
            "total_entries": total,
            "by_outcome": {row["outcome"]: row["cnt"] for row in by_outcome_rows},
            "top_tags": {row["tag"]: row["cnt"] for row in by_tag_rows},
            "latest_entry": latest,
            "latest_entry_id": latest["entry_id"] if latest else "",
        }

    @staticmethod
    def _row_to_entry(row) -> ReflectionMemoryEntry:
        return ReflectionMemoryEntry(
            entry_id=row["entry_id"],
            task_id=row["task_id"],
            trace_id=row["trace_id"],
            outcome=row["outcome"],
            summary=row["summary"],
            reflection=row["reflection"],
            author=row["author"],
            source=row["source"],
            tags=json.loads(row["tags_json"] or "[]"),
            metadata=json.loads(row["metadata_json"] or "{}"),
            task_snapshot=json.loads(row["task_snapshot_json"] or "{}"),
            trace_snapshot=json.loads(row["trace_snapshot_json"] or "{}"),
            created_at=row["created_at"],
        )
