"""Server-side persistence for the canonical security context of a task.

Security state is deliberately stored separately from user/task payload data.
The task payload is untrusted workflow data; this table is authoritative
security state used by recovery and resume paths.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from botboy.security_context import SecurityContext


class TaskSecurityStore:
    """Persist and reconstruct SecurityContext records using the task DB."""

    def __init__(self, task_store: Any) -> None:
        self.task_store = task_store
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        conn = self.task_store._get_conn()
        conn.execute(
            """CREATE TABLE IF NOT EXISTS task_security_contexts (
                task_id TEXT PRIMARY KEY,
                security_context_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES tasks(task_id)
            )"""
        )
        conn.commit()

    def save(self, context: SecurityContext) -> None:
        if not context.task_id:
            raise ValueError("SecurityContext.task_id is required for persistence")
        payload = json.dumps(context.to_dict(), sort_keys=True)
        now = self.task_store._now()
        conn = self.task_store._get_conn()
        conn.execute(
            """INSERT INTO task_security_contexts
               (task_id, security_context_json, created_at, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(task_id) DO UPDATE SET
                 security_context_json = excluded.security_context_json,
                 updated_at = excluded.updated_at""",
            (context.task_id, payload, now, now),
        )
        conn.commit()

    def load(self, task_id: str) -> Optional[SecurityContext]:
        conn = self.task_store._get_conn()
        row = conn.execute(
            "SELECT security_context_json FROM task_security_contexts WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if not row:
            return None
        try:
            payload = json.loads(row["security_context_json"])
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        return SecurityContext.from_dict(payload)

    def delete(self, task_id: str) -> None:
        conn = self.task_store._get_conn()
        conn.execute("DELETE FROM task_security_contexts WHERE task_id = ?", (task_id,))
        conn.commit()
