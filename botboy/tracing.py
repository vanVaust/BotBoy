"""BotBoy tracing store and context propagation primitives."""
from __future__ import annotations

import secrets
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from botboy.db_mixin import _SQLiteMixin

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trace_runs (
    run_id          TEXT PRIMARY KEY,
    trace_id        TEXT NOT NULL,
    task_id         TEXT NOT NULL DEFAULT '',
    command         TEXT NOT NULL,
    principal       TEXT NOT NULL DEFAULT 'anonymous',
    request_id      TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'running',
    cmd_type        TEXT NOT NULL DEFAULT '',
    summary         TEXT NOT NULL DEFAULT '',
    payload_ref     TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trace_runs_trace_id ON trace_runs(trace_id);
CREATE INDEX IF NOT EXISTS idx_trace_runs_principal ON trace_runs(principal);
CREATE INDEX IF NOT EXISTS idx_trace_runs_request_id ON trace_runs(request_id);
CREATE INDEX IF NOT EXISTS idx_trace_runs_status ON trace_runs(status);

CREATE TABLE IF NOT EXISTS trace_spans (
    span_id          TEXT PRIMARY KEY,
    run_id           TEXT NOT NULL,
    trace_id         TEXT NOT NULL,
    parent_span_id   TEXT NOT NULL DEFAULT '',
    component        TEXT NOT NULL,
    event_type       TEXT NOT NULL,
    principal        TEXT NOT NULL DEFAULT 'anonymous',
    request_id       TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'running',
    payload_ref      TEXT NOT NULL DEFAULT '',
    started_at       TEXT NOT NULL,
    ended_at         TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(run_id) REFERENCES trace_runs(run_id)
);
CREATE INDEX IF NOT EXISTS idx_trace_spans_run_id ON trace_spans(run_id);
CREATE INDEX IF NOT EXISTS idx_trace_spans_trace_id ON trace_spans(trace_id);
CREATE INDEX IF NOT EXISTS idx_trace_spans_parent ON trace_spans(parent_span_id);
CREATE INDEX IF NOT EXISTS idx_trace_spans_component ON trace_spans(component);
"""


@dataclass
class TraceContext:
    run_id: str
    trace_id: str
    task_id: str
    principal: str
    request_id: str
    root_span_id: str = ""
    current_span_id: str = ""


@dataclass
class TraceRunRecord:
    run_id: str
    trace_id: str
    task_id: str
    command: str
    principal: str
    request_id: str
    status: str
    cmd_type: str
    summary: str
    payload_ref: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "trace_id": self.trace_id,
            "task_id": self.task_id,
            "command": self.command,
            "principal": self.principal,
            "request_id": self.request_id,
            "status": self.status,
            "cmd_type": self.cmd_type,
            "summary": self.summary,
            "payload_ref": self.payload_ref,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class TraceSpanRecord:
    span_id: str
    run_id: str
    trace_id: str
    parent_span_id: str
    component: str
    event_type: str
    principal: str
    request_id: str
    status: str
    payload_ref: str
    started_at: str
    ended_at: str

    def to_dict(self) -> dict:
        return {
            "span_id": self.span_id,
            "run_id": self.run_id,
            "trace_id": self.trace_id,
            "parent_span_id": self.parent_span_id,
            "component": self.component,
            "event_type": self.event_type,
            "principal": self.principal,
            "request_id": self.request_id,
            "status": self.status,
            "payload_ref": self.payload_ref,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
        }


_current_trace: ContextVar[Optional[TraceContext]] = ContextVar("botboy_trace_context", default=None)


def bind_trace_context(ctx: TraceContext) -> Token:
    return _current_trace.set(ctx)


def get_trace_context() -> Optional[TraceContext]:
    return _current_trace.get()


def reset_trace_context(token: Token) -> None:
    _current_trace.reset(token)


class TraceStore(_SQLiteMixin):
    """Persistent trace run and span store."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self._init_connection_pool(db_path, _SCHEMA)
        self._ensure_trace_schema()

    def _ensure_trace_schema(self) -> None:
        conn = self._get_conn()
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(trace_runs)").fetchall()}
        if "task_id" not in columns:
            conn.execute("ALTER TABLE trace_runs ADD COLUMN task_id TEXT NOT NULL DEFAULT ''")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trace_runs_task_id ON trace_runs(task_id)")
        conn.commit()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}-{secrets.token_hex(8)}"

    def create_run(
        self,
        command: str,
        principal: str = "anonymous",
        request_id: str = "",
        trace_id: str = "",
        task_id: str = "",
    ) -> TraceContext:
        run_id = self._new_id("run")
        resolved_trace_id = trace_id or self._new_id("trace")
        ts = self._now()
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO trace_runs (run_id, trace_id, task_id, command, principal, request_id, status, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (run_id, resolved_trace_id, task_id or "", command, principal or "anonymous", request_id or "", "running", ts, ts),
        )
        conn.commit()
        return TraceContext(
            run_id=run_id,
            trace_id=resolved_trace_id,
            task_id=task_id or "",
            principal=principal or "anonymous",
            request_id=request_id or "",
        )

    def start_span(
        self,
        ctx: TraceContext,
        *,
        component: str,
        event_type: str,
        parent_span_id: str = "",
        payload_ref: str = "",
    ) -> str:
        span_id = self._new_id("span")
        ts = self._now()
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO trace_spans (span_id, run_id, trace_id, parent_span_id, component, event_type, principal, request_id, status, payload_ref, started_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                span_id,
                ctx.run_id,
                ctx.trace_id,
                parent_span_id,
                component,
                event_type,
                ctx.principal,
                ctx.request_id,
                "running",
                payload_ref,
                ts,
            ),
        )
        conn.commit()
        return span_id

    def finish_span(self, span_id: str, *, status: str, payload_ref: str = "") -> None:
        ended_at = self._now()
        conn = self._get_conn()
        conn.execute(
            "UPDATE trace_spans SET status = ?, payload_ref = ?, ended_at = ? WHERE span_id = ?",
            (status, payload_ref, ended_at, span_id),
        )
        conn.commit()

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        cmd_type: str = "",
        summary: str = "",
        payload_ref: str = "",
    ) -> None:
        updated_at = self._now()
        conn = self._get_conn()
        conn.execute(
            "UPDATE trace_runs SET status = ?, cmd_type = ?, summary = ?, payload_ref = ?, updated_at = ? WHERE run_id = ?",
            (status, cmd_type, summary, payload_ref, updated_at, run_id),
        )
        conn.commit()

    def list_runs(
        self,
        limit: int = 20,
        offset: int = 0,
        task_id: Optional[str] = None,
        principal: Optional[str] = None,
        request_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Tuple[List[TraceRunRecord], int]:
        conditions = []
        params: list = []
        if task_id:
            conditions.append("task_id = ?")
            params.append(task_id)
        if principal:
            conditions.append("principal = ?")
            params.append(principal)
        if request_id:
            conditions.append("request_id = ?")
            params.append(request_id)
        if status:
            conditions.append("status = ?")
            params.append(status)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        conn = self._get_conn()
        total = conn.execute(f"SELECT COUNT(*) FROM trace_runs {where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM trace_runs {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return [self._row_to_run(r) for r in rows], total

    def get_run(self, run_id: str) -> Optional[dict]:
        row = self._fetchone("SELECT * FROM trace_runs WHERE run_id = ?", (run_id,))
        if not row:
            return None
        spans = self._fetchall(
            "SELECT * FROM trace_spans WHERE run_id = ? ORDER BY started_at ASC",
            (run_id,),
        )
        return {
            "run": self._row_to_run(row).to_dict(),
            "spans": [self._row_to_span(span).to_dict() for span in spans],
        }

    def summary(self) -> dict:
        conn = self._get_conn()
        total_runs = conn.execute("SELECT COUNT(*) FROM trace_runs").fetchone()[0]
        recent_rows = conn.execute(
            "SELECT * FROM trace_runs ORDER BY created_at DESC LIMIT 5"
        ).fetchall()
        latest_row = recent_rows[0] if recent_rows else None
        by_status_rows = conn.execute(
            "SELECT status, COUNT(*) AS cnt FROM trace_runs GROUP BY status ORDER BY cnt DESC"
        ).fetchall()
        span_count = conn.execute("SELECT COUNT(*) FROM trace_spans").fetchone()[0]
        latest_run = self._row_to_run(latest_row).to_dict() if latest_row else None
        return {
            "available": True,
            "total_runs": total_runs,
            "total_spans": span_count,
            "by_status": {row["status"]: row["cnt"] for row in by_status_rows},
            "recent_runs": [self._row_to_run(row).to_dict() for row in recent_rows],
            "latest_run": latest_run,
            "latest_run_id": latest_run["run_id"] if latest_run else "",
            "latest_status": latest_run["status"] if latest_run else "",
        }

    @staticmethod
    def _row_to_run(row) -> TraceRunRecord:
        return TraceRunRecord(
            run_id=row["run_id"],
            trace_id=row["trace_id"],
            task_id=row["task_id"],
            command=row["command"],
            principal=row["principal"],
            request_id=row["request_id"],
            status=row["status"],
            cmd_type=row["cmd_type"],
            summary=row["summary"],
            payload_ref=row["payload_ref"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_span(row) -> TraceSpanRecord:
        return TraceSpanRecord(
            span_id=row["span_id"],
            run_id=row["run_id"],
            trace_id=row["trace_id"],
            parent_span_id=row["parent_span_id"],
            component=row["component"],
            event_type=row["event_type"],
            principal=row["principal"],
            request_id=row["request_id"],
            status=row["status"],
            payload_ref=row["payload_ref"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
        )
