"""Final authorization gate immediately before gateway command execution.

This is intentionally installed at the gateway package boundary so every
CommandExecutionService route invocation reached through the authenticated
FastAPI gateway crosses one final server-side check after validation and task
context creation.
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from botboy.approval_store import ApprovalStore
from botboy.command_execution_service import CommandExecutionService
from botboy.task_security import TaskSecurityStore


_INSTALLED = False


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

    required = service._approval_required(command)
    if not required:
        return

    if not service._has_server_approval(approval_context):
        raise HTTPException(status_code=403, detail="Server-issued approval required at final execution boundary")

    task_principal = str(getattr(task_ctx, "principal", "") or "")
    if task_principal != str(principal_id):
        raise HTTPException(status_code=403, detail="Execution principal does not own the task")
    if not str(getattr(task_ctx, "task_id", "") or "").strip():
        raise HTTPException(status_code=403, detail="Execution task context is invalid")

    security_store = TaskSecurityStore(store)
    context = security_store.load(task_ctx.task_id)
    if context is None:
        raise HTTPException(status_code=403, detail="Persisted security context is missing")
    if context.principal_id != principal_id:
        raise HTTPException(status_code=403, detail="Persisted security principal mismatch")
    if str(context.org_id or "default") != str(getattr(task_ctx, "org_id", "default") or "default"):
        raise HTTPException(status_code=403, detail="Persisted security tenant mismatch")
    if context.task_id != task_ctx.task_id:
        raise HTTPException(status_code=403, detail="Persisted security task mismatch")

    approval_id = str(approval_context.get("approval_id", "")).strip()
    try:
        approval = ApprovalStore(store).get_consumed_if_valid(
            approval_id,
            task_id=task_ctx.task_id,
            principal_id=principal_id,
            org_id=str(context.org_id or "default"),
            command=command,
            capability="task.execute",
            authorization_version=int(context.authorization_version),
        )
    except Exception as exc:
        raise HTTPException(status_code=403, detail="Approval grant could not be verified") from exc
    if approval is None:
        raise HTTPException(status_code=403, detail="Approval grant is not valid at the final execution boundary")


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
