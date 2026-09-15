"""Server-issued, task-bound, single-use authorization approvals."""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional


DEFAULT_APPROVAL_TTL_SECONDS = 300


def command_fingerprint(command: str) -> str:
    """Return a stable digest of the exact command being authorized."""
    return hashlib.sha256(str(command or "").encode("utf-8")).hexdigest()


class ApprovalStore:
    """Persist approvals and consume them atomically at the execution boundary."""

    def __init__(self, task_store: Any, *, ttl_seconds: int = DEFAULT_APPROVAL_TTL_SECONDS) -> None:
        self.task_store = task_store
        self.ttl_seconds = max(1, int(ttl_seconds))
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        conn = self.task_store._get_conn()
        conn.execute(
            """CREATE TABLE IF NOT EXISTS task_approvals (
                approval_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                principal_id TEXT NOT NULL,
                org_id TEXT NOT NULL DEFAULT 'default',
                capability TEXT NOT NULL,
                command_fingerprint TEXT NOT NULL,
                authorization_version INTEGER NOT NULL DEFAULT 1,
                issued_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                consumed_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(task_id) REFERENCES tasks(task_id)
            )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_approvals_task ON task_approvals(task_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_approvals_expiry ON task_approvals(expires_at)")
        conn.commit()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _iso(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat()

    def issue(
        self,
        *,
        task_id: str,
        principal_id: str,
        org_id: str,
        command: str,
        capability: str = "task.execute",
        authorization_version: int = 1,
        ttl_seconds: Optional[int] = None,
    ) -> dict:
        if not task_id or not principal_id or not capability:
            raise ValueError("task_id, principal_id and capability are required")
        issued = self._now()
        expires = issued + timedelta(seconds=max(1, int(ttl_seconds or self.ttl_seconds)))
        approval_id = secrets.token_urlsafe(32)
        conn = self.task_store._get_conn()
        conn.execute(
            """INSERT INTO task_approvals
               (approval_id, task_id, principal_id, org_id, capability,
                command_fingerprint, authorization_version, issued_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                approval_id,
                task_id,
                principal_id,
                org_id or "default",
                capability,
                command_fingerprint(command),
                int(authorization_version),
                self._iso(issued),
                self._iso(expires),
            ),
        )
        conn.commit()
        return {
            "approval_id": approval_id,
            "task_id": task_id,
            "principal_id": principal_id,
            "org_id": org_id or "default",
            "capability": capability,
            "issued_at": self._iso(issued),
            "expires_at": self._iso(expires),
            "authorization_version": int(authorization_version),
        }

    def consume_if_valid(
        self,
        approval_id: str,
        *,
        task_id: str,
        principal_id: str,
        org_id: str,
        command: str,
        capability: str = "task.execute",
        authorization_version: Optional[int] = None,
    ) -> Optional[dict]:
        """Atomically consume an approval if every binding still matches."""
        if not approval_id or not task_id or not principal_id:
            return None
        now = self._iso(self._now())
        fingerprint = command_fingerprint(command)
        conn = self.task_store._get_conn()
        clauses = [
            "approval_id = ?",
            "task_id = ?",
            "principal_id = ?",
            "org_id = ?",
            "capability = ?",
            "command_fingerprint = ?",
            "consumed_at = ''",
            "expires_at > ?",
        ]
        params = [
            approval_id,
            task_id,
            principal_id,
            org_id or "default",
            capability,
            fingerprint,
            now,
        ]
        if authorization_version is not None:
            clauses.append("authorization_version = ?")
            params.append(int(authorization_version))
        cur = conn.execute(
            "UPDATE task_approvals SET consumed_at = ? WHERE " + " AND ".join(clauses),
            [now, *params],
        )
        conn.commit()
        if cur.rowcount != 1:
            return None
        row = conn.execute(
            "SELECT approval_id, task_id, principal_id, org_id, capability, "
            "issued_at, expires_at, consumed_at, authorization_version "
            "FROM task_approvals WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
        if not row:
            return None
        return dict(row)
