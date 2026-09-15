"""Authorization boundaries for task recovery and child-task operations.

These methods are intentionally patched onto the gateway task-store proxy so
security-sensitive TaskStore methods cannot bypass the gateway identity
context through the proxy's generic attribute forwarding.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from fastapi import HTTPException

from botboy.gateway.app_context import (
    _TaskStoreAuthorizationProxy,
    current_gateway_principal,
)
from botboy.security_context import SecurityContext
from botboy.task_security import TaskSecurityStore


def _authorized_record(proxy: _TaskStoreAuthorizationProxy, task_id: str) -> Any:
    record = proxy._store.get_task(task_id)
    return record if proxy._authorized(record) else None


def _require_authorized_task(proxy: _TaskStoreAuthorizationProxy, task_id: str) -> Any:
    record = _authorized_record(proxy, task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return record


def _create_child_task(
    proxy: _TaskStoreAuthorizationProxy,
    parent_task_id: str,
    *,
    worker_id: str,
    title: str,
    **kwargs: Any,
):
    parent = _require_authorized_task(proxy, parent_task_id)
    parent_security = TaskSecurityStore(proxy._store).load(parent.task_id)

    # Child identity and tenant are derived exclusively from the authorized
    # parent. Client-supplied principal/org values are never authoritative.
    principal = str(
        parent_security.principal_id
        if parent_security
        else getattr(parent, "principal", "anonymous")
    )
    org_id = str(
        parent_security.org_id
        if parent_security
        else getattr(parent, "org_id", "default") or "default"
    )

    kwargs.pop("principal", None)
    kwargs.pop("org_id", None)
    child = proxy._store.create_child_task(
        parent_task_id,
        worker_id=worker_id,
        title=title,
        principal=principal,
        **kwargs,
    )

    # TaskStore.create_child_task predates tenant-aware authorization and
    # defaults to the default tenant. Correct it under the authorized parent
    # boundary; callers cannot choose this value.
    if str(getattr(child, "org_id", "default") or "default") != org_id:
        child = proxy._store.update_task(child.task_id, org_id=org_id)

    if parent_security is not None:
        # A parent approval is never inherited by a child execution context.
        child_security = replace(
            parent_security,
            task_id=child.task_id,
            parent_task_id=parent.task_id,
            approval_id="",
            approval_scope=frozenset(),
            approval_expires_at=None,
        )
    else:
        child_security = SecurityContext.from_legacy(
            principal=principal,
            org_id=org_id,
            roles=list(proxy._roles()),
            request_id=str(getattr(child, "request_id", "") or ""),
            task_id=child.task_id,
            parent_task_id=parent.task_id,
            auth_source="task_parent",
        )
    TaskSecurityStore(proxy._store).save(child_security)
    return child


def _recover_stale_worker_task(
    proxy: _TaskStoreAuthorizationProxy,
    task_id: str,
    *,
    principal: str = "anonymous",
    request_id: str = "",
    run_id: str = "",
    lease_timeout_s: int = 900,
):
    # Recovery is intentionally non-oracular: an unauthorized task looks like
    # a missing task and is never mutated.
    if _authorized_record(proxy, task_id) is None:
        return None
    effective_principal = current_gateway_principal() if proxy._auth_enabled else principal
    if not effective_principal:
        raise HTTPException(
            status_code=403,
            detail="Recovery requires an authenticated principal",
        )
    return proxy._store.recover_stale_worker_task(
        task_id,
        principal=effective_principal,
        request_id=request_id,
        run_id=run_id,
        lease_timeout_s=lease_timeout_s,
    )


def _reassign_task(
    proxy: _TaskStoreAuthorizationProxy,
    task_id: str,
    *,
    worker_id: str,
    principal: str = "anonymous",
    request_id: str = "",
    run_id: str = "",
):
    _require_authorized_task(proxy, task_id)
    if proxy._auth_enabled and not proxy._is_system_or_admin():
        raise HTTPException(
            status_code=403,
            detail="Task reassignment requires system or admin authorization",
        )
    effective_principal = current_gateway_principal() if proxy._auth_enabled else principal
    return proxy._store.reassign_task(
        task_id,
        worker_id=worker_id,
        principal=effective_principal,
        request_id=request_id,
        run_id=run_id,
    )


# A worker handoff may carry a parent approval context for observability, but a
# server-issued approval must never become authorization for the child task.
# Legacy/manual contexts are retained for compatibility because the execution
# boundary already rejects them as non-server-issued authorization.
def _build_child_approval_context(self, approval_context):
    context = dict(approval_context or {})
    is_server_approval = bool(
        context.get("source") == "approval_store"
        and str(context.get("approval_id", "")).strip()
        and context.get("approval_scope") == "task.execute"
    )
    if is_server_approval:
        return {
            "granted": False,
            "explicit": False,
            "source": "worker_handoff",
            "reason": "child_requires_independent_approval",
        }

    inherited_scope = bool(context.get("approval_scope")) or (
        bool(context.get("granted")) and bool(context.get("explicit"))
    )
    context["granted"] = inherited_scope
    if inherited_scope and "approval_scope" not in context:
        context["approval_scope"] = "parent_inherited"
    context.setdefault("source", "worker_handoff")
    context.setdefault("reason", "child_execution")
    return context


_TaskStoreAuthorizationProxy.create_child_task = _create_child_task
_TaskStoreAuthorizationProxy.recover_stale_worker_task = _recover_stale_worker_task
_TaskStoreAuthorizationProxy.reassign_task = _reassign_task

# WorkerHandoffService currently receives a task store directly in some
# internal paths, so secure its child-approval transformation independently
# of whether the task-store proxy is present.
try:
    from botboy.worker_handoff_service import WorkerHandoffService

    WorkerHandoffService._build_child_approval_context = _build_child_approval_context
except ImportError:
    # The gateway remains importable in minimal installations where the
    # optional handoff service is not packaged.
    pass
