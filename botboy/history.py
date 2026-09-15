"""TaskHistory — persistent command history with pagination and stats."""
from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

from botboy.db_mixin import _SQLiteMixin

_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    command     TEXT    NOT NULL,
    output      TEXT,
    cmd_type    TEXT    NOT NULL DEFAULT 'unknown',
    success     INTEGER NOT NULL DEFAULT 1,
    latency_ms  REAL    NOT NULL DEFAULT 0,
    principal   TEXT    NOT NULL DEFAULT 'anonymous',
    request_id  TEXT    NOT NULL DEFAULT '',
    task_id     TEXT    NOT NULL DEFAULT '',
    org_id      TEXT    NOT NULL DEFAULT 'default',
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_th_created  ON task_history(created_at);
CREATE INDEX IF NOT EXISTS idx_th_cmd_type ON task_history(cmd_type);
CREATE INDEX IF NOT EXISTS idx_th_success  ON task_history(success);
CREATE INDEX IF NOT EXISTS idx_th_principal ON task_history(principal);
"""

@dataclass
class HistoryRecord:
    id: int
    command: str
    output: Optional[str]
    cmd_type: str
    success: bool
    latency_ms: float
    principal: str
    request_id: str
    task_id: str
    org_id: str
    created_at: str

    def to_dict(self) -> dict:
        return {"id": self.id, "command": self.command, "output": self.output,
                "cmd_type": self.cmd_type, "success": self.success,
                "latency_ms": self.latency_ms, "principal": self.principal,
                "request_id": self.request_id, "task_id": self.task_id,
                "org_id": self.org_id, "created_at": self.created_at}

class TaskHistory(_SQLiteMixin):
    """Persistent task history with pagination, filtering, and statistics."""
    def __init__(self, db_path: str = ":memory:") -> None:
        self._init_connection_pool(db_path, _SCHEMA)
        self._ensure_history_schema()

    def _ensure_history_schema(self) -> None:
        conn = self._get_conn()
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(task_history)").fetchall()}
        if "request_id" not in columns:
            conn.execute("ALTER TABLE task_history ADD COLUMN request_id TEXT NOT NULL DEFAULT ''")
        if "task_id" not in columns:
            conn.execute("ALTER TABLE task_history ADD COLUMN task_id TEXT NOT NULL DEFAULT ''")
        if "org_id" not in columns:
            conn.execute("ALTER TABLE task_history ADD COLUMN org_id TEXT NOT NULL DEFAULT 'default'")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_th_principal ON task_history(principal)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_th_request_id ON task_history(request_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_th_task_id ON task_history(task_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_th_org_id ON task_history(org_id)")
        conn.commit()

    def record(self, command: str, output: Optional[str] = None, success: bool = True,
               cmd_type: str = "unknown", latency_ms: float = 0.0,
               principal: str = "anonymous", request_id: str = "", task_id: str = "",
               org_id: str = "default") -> int:
        ts = datetime.now(timezone.utc).isoformat()
        args = (command, output, cmd_type, int(success), latency_ms, principal,
                request_id, task_id, org_id or "default", ts)
        sql = ("INSERT INTO task_history (command, output, cmd_type, success, latency_ms, "
               "principal, request_id, task_id, org_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)")
        if self._is_memory:
            with self._shared_lock:
                cur = self._shared_conn.execute(sql, args)
                self._shared_conn.commit()
                return cur.lastrowid
        conn = self._get_conn()
        cur = conn.execute(sql, args)
        conn.commit()
        return cur.lastrowid

    def get(self, record_id: int) -> Optional[HistoryRecord]:
        row = self._fetchone("SELECT * FROM task_history WHERE id = ?", (record_id,))
        return self._row_to_record(row) if row else None

    def list(self, limit: int = 20, offset: int = 0, success: Optional[bool] = None,
             cmd_type: Optional[str] = None, search: Optional[str] = None,
             principal: Optional[str] = None, request_id: Optional[str] = None,
             task_id: Optional[str] = None, org_id: Optional[str] = None) -> Tuple[List[HistoryRecord], int]:
        conn = self._get_conn(); conditions, params = [], []
        if success is not None: conditions.append("success = ?"); params.append(int(success))
        if cmd_type: conditions.append("cmd_type = ?"); params.append(cmd_type)
        if search: conditions.append("(command LIKE ? OR output LIKE ?)"); params.extend([f"%{search}%", f"%{search}%"])
        if principal: conditions.append("principal = ?"); params.append(principal)
        if request_id: conditions.append("request_id = ?"); params.append(request_id)
        if task_id: conditions.append("task_id = ?"); params.append(task_id)
        if org_id: conditions.append("org_id = ?"); params.append(org_id)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        total = conn.execute(f"SELECT COUNT(*) FROM task_history {where}", params).fetchone()[0]
        rows = conn.execute(f"SELECT * FROM task_history {where} ORDER BY id DESC LIMIT ? OFFSET ?",
                            params + [limit, offset]).fetchall()
        return [self._row_to_record(r) for r in rows], total

    def stats(self, *, principal: Optional[str] = None, org_id: Optional[str] = None) -> dict:
        conn = self._get_conn(); filters, params = [], []
        if principal: filters.append("principal = ?"); params.append(principal)
        if org_id: filters.append("org_id = ?"); params.append(org_id)
        where = (" WHERE " + " AND ".join(filters)) if filters else ""
        row = conn.execute("SELECT COUNT(*) total, SUM(success) successes, "
                           "COUNT(*) - SUM(success) failures, AVG(latency_ms) avg_latency "
                           "FROM task_history" + where, params).fetchone()
        by_type = conn.execute("SELECT cmd_type, COUNT(*) cnt FROM task_history" + where +
                               " GROUP BY cmd_type ORDER BY cnt DESC", params).fetchall()
        by_principal = conn.execute("SELECT principal, COUNT(*) cnt FROM task_history" + where +
                                    " GROUP BY principal ORDER BY cnt DESC", params).fetchall()
        return {"total": row["total"], "success_count": row["successes"] or 0,
                "failure_count": row["failures"] or 0,
                "success_rate": (row["successes"] or 0) / row["total"] if row["total"] > 0 else 0.0,
                "avg_latency_ms": row["avg_latency"] or 0.0,
                "by_type": {r["cmd_type"]: r["cnt"] for r in by_type},
                "by_principal": {r["principal"]: r["cnt"] for r in by_principal}}

    def purge(self, older_than_days: int = 90) -> int:
        conn = self._get_conn(); cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
        cur = conn.execute("DELETE FROM task_history WHERE created_at < ?", (cutoff,)); conn.commit(); return cur.rowcount

    def close(self) -> None: super().close()

    @staticmethod
    def _row_to_record(row) -> HistoryRecord:
        return HistoryRecord(id=row["id"], command=row["command"], output=row["output"],
                             cmd_type=row["cmd_type"], success=bool(row["success"]),
                             latency_ms=row["latency_ms"], principal=row["principal"],
                             request_id=row["request_id"], task_id=row["task_id"],
                             org_id=row["org_id"] if "org_id" in row.keys() else "default",
                             created_at=row["created_at"])

def make_history_router(history: TaskHistory):
    """Create a FastAPI router for history endpoints (optional)."""
    try:
        from fastapi import APIRouter, Query, HTTPException, Request
        from botboy.gateway.app_context import current_gateway_org, current_gateway_principal, current_gateway_roles
        router = APIRouter(prefix="/api/history", tags=["history"])
        def _scope():
            roles = {str(r).lower() for r in current_gateway_roles()}
            if "admin" in roles: return None, None
            return current_gateway_principal(), current_gateway_org()
        @router.get("")
        async def list_history(request: Request, limit: int = Query(20, ge=1, le=200), offset: int = Query(0, ge=0),
                               success: Optional[bool] = None, cmd_type: Optional[str] = None, search: Optional[str] = None):
            principal, org = _scope()
            records, total = history.list(limit=limit, offset=offset, success=success, cmd_type=cmd_type,
                                           search=search, principal=principal, org_id=org)
            return {"records": [r.to_dict() for r in records], "total": total}
        @router.get("/stats")
        async def history_stats(request: Request):
            principal, org = _scope(); return history.stats(principal=principal, org_id=org)
        @router.get("/{record_id}")
        async def get_record(request: Request, record_id: int):
            rec = history.get(record_id)
            principal, org = _scope()
            if not rec or (principal is not None and (rec.principal != principal or rec.org_id != org)):
                raise HTTPException(status_code=404, detail="Record not found")
            return rec.to_dict()
        return router
    except ImportError:
        return None
