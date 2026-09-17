"""Final authorization gate immediately before gateway command execution.

This is intentionally installed at the gateway package boundary so every
CommandExecutionService route invocation reached through the authenticated
FastAPI gateway crosses one final server-side check after validation and task
context creation.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from botboy.command_execution_service import CommandExecutionService
from botboy.security_context import SecurityContext
from botboy.task_security import TaskSecurityStore


_INSTALLED = False


def _fingerprint(command: str) -> str:
    return hashlib.sha256(str(command or "").encode("utf-8")).hexdigest()


def _approval_row(store: Any, approval_id: str) -> Any:
    try:
        return store._get_conn().execute(
            "SELECT approval_id, task_id, principal_id, org_id, capability, "
            "command_fingerprint, authorization_version, expires_at, consumed_at "
            "FROM task_approvals WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
    except Exception:
        return None


def _require_final_authorization(
    service: CommandExecutionService,
    command: str,
    *,
    principal_id: str,
    approval_context: dict,
    task_ctx: Any,
) -> None:
    bot = service.bot
    store = getattr(bot, "task_store", None)
    if store is None or task_ctx is None:
        raise HTTPException(status_code=403, detail="Execution security context is unavailable")

    task = store.get_task(task_ctx.task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if str(getattr(task, "principal", "")) != str(principal_id):
        raise HTTPException(status_code=403, detail="Execution principal does not own the task")

    security_store = TaskSecurityStore(store)
    context = security_store.load(task_ctx.task_id)
    if context is None:
        raise HTTPException(status_code=403, detail="Persisted security context is missing")
    if context.principal_id != principal_id:
        raise HTTPException(status_code=403, detail="Persisted security principal mismatch")
    if str(context.org_id or "default") != str(getattr(task, "org_id", "default") or "default"):
        raise HTTPException(status_code=403, detail="Persisted security tenant mismatch")
    if context.task_id != task_ctx.task_id:
        raise HTTPException(status_code=403, detail="Persisted security task mismatch")

    required = service._approval_required(command)
    if not required:
        return

    if not service._has_server_approval(approval_context):
        raise HTTPException(status_code=403, detail="Server-issued approval required at final execution boundary")

    approval_id = str(approval_context.get("approval_id", "")).strip()
    row = _approval_row(store, approval_id)
    if row is None:
        raise HTTPException(status_code=403, detail="Approval grant does not exist")
    if str(row["task_id"]) != str(task_ctx.task_id):
        raise HTTPException(status_code=403, detail="Approval is bound to another task")
    if str(row["principal_id"]) != str(principal_id):
        raise HTTPException(status_code=403, detail="Approval principal mismatch")
    if str(row["org_id"] or "default") != str(context.org_id or "default"):
        raise HTTPException(status_code=403, detail="Approval tenant mismatch")
    if str(row["capability"]) != "task.execute":
        raise HTTPException(status_code=403, detail="Approval capability mismatch")
    if str(row["command_fingerprint"]) != _fingerprint(command):
        raise HTTPException(status_code=403, detail="Approval command mismatch")
    if str(row["authorization_version"]) != str(context.authorization_version):
        raise HTTPException(status_code=403, detail="Approval authorization version mismatch")
    if not str(row["consumed_at"] or "").strip():
        raise HTTPException(status_code=403, detail="Approval was not atomically consumed before execution")
    try:
        expiry = datetime.fromisoformat(str(row["expires_at"]))
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry <= datetime.now(timezone.utc):
            raise HTTPException(status_code=403, detail="Approval has expired")
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Approval expiry is invalid") from exc


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    original = CommandExecutionService._run_route

    async def guarded(self: CommandExecutionService, command: str, **kwargs: Any):
        _require_final_authorization(
            self,
            command,
            principal_id=str(kwargs.get("principal_id", "") or ""),
            approval_context=dict(kwargs.get("approval_context") or {}),
            task_ctx=kwargs.get("task_ctx"),
        )
        return await original(self, command, **kwargs)

    CommandExecutionService._run_route = guarded
    _INSTALLED = True


install()
