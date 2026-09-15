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
    """Persist and reconstruct SecurityContext records using the task DB.

    Existing security state is monotonic: a later write may retain or reduce
    privileges, but it may not silently change task ownership/tenant or expand
    roles, scopes, capabilities, or approval scope.
    """

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

    @staticmethod
    def _validate_transition(previous: SecurityContext, current: SecurityContext) -> None:
        if previous.task_id != current.task_id:
            raise ValueError("SecurityContext task_id cannot change")
        if previous.principal_id != current.principal_id:
            raise ValueError("SecurityContext principal cannot change")
        if previous.org_id != current.org_id:
            raise ValueError("SecurityContext org cannot change")
        if not previous.roles.issuperset(current.roles):
            raise ValueError("SecurityContext roles cannot be expanded")
        if not previous.scopes.issuperset(current.scopes):
            raise ValueError("SecurityContext scopes cannot be expanded")
        if not previous.capabilities.issuperset(current.capabilities):
            raise ValueError("SecurityContext capabilities cannot be expanded")
        if not previous.approval_scope.issuperset(current.approval_scope):
            raise ValueError("SecurityContext approval scope cannot be expanded")
        try:
            previous_version = int(previous.authorization_version)
            current_version = int(current.authorization_version)
        except (TypeError, ValueError) as exc:
            raise ValueError("SecurityContext authorization_version must be numeric") from exc
        if current_version < previous_version:
            raise ValueError("SecurityContext authorization version cannot decrease")

        # An approval is a separate, server-issued grant. Persisting a context
        # must never manufacture one or replace one with a different grant.
        if previous.approval_id and current.approval_id != previous.approval_id:
            raise ValueError("Existing approval binding cannot be replaced")

    def save(self, context: SecurityContext) -> None:
        if not context.task_id:
            raise ValueError("SecurityContext.task_id is required for persistence")
        conn = self.task_store._get_conn()
        existing = conn.execute(
            "SELECT security_context_json FROM task_security_contexts WHERE task_id = ?",
            (context.task_id,),
        ).fetchone()
        if existing:
            try:
                payload = json.loads(existing["security_context_json"])
                previous = SecurityContext.from_dict(payload)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError("Stored SecurityContext is invalid") from exc
            self._validate_transition(previous, context)

        payload = json.dumps(context.to_dict(), sort_keys=True)
        now = self.task_store._now()
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
