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
    current_gateway_org,
    current_gateway_principal,
)
from botboy.security_context import SecurityContext
from botboy.task_security import TaskSecurityStore


def _require_authorized_task(proxy: _TaskStoreAuthorizationProxy, task_id: str) -> Any:
    record = proxy._store.get_task(task_id)
    if not proxy._authorized(record):
        raise HTTPException(status_code=404, detail="Task not found")
    return record


def _create_child_task(proxy: _TaskStoreAuthorizationProxy, parent_task_id: str, *, worker_id: str, title: str, **kwargs: Any):
    parent = _require_authorized_task(proxy, parent_task_id)
    parent_security = TaskSecurityStore(proxy._store).load(parent.task_id)

    # The caller cannot choose the child identity or tenant. Child execution
    # inherits the parent's server-side identity, never a client-supplied one.
    principal = str(parent_security.principal_id if parent_security else getattr(parent, "principal", "anonymous"))
    org_id = str(parent_security.org_id if parent_security else getattr(parent, "org_id", "default") or "default")

    kwargs.pop("principal", None)
    child = proxy._store.create_child_task(
        parent_task_id,
        worker_id=worker_id,
        title=title,
        principal=principal,
        **kwargs,
    )

    # create_child_task predates tenant-aware gateway authorization and uses
    # TaskStore's default org. Correct it immediately under the already
    # authorized parent boundary; callers cannot select the value.
    if str(getattr(child, "org_id", "default") or "default") != org_id:
        child = proxy._store.update_task(child.task_id, org_id=org_id)

    if parent_security is not None:
        # Approval is deliberately NOT inherited by a child task.
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


def _recover_stale_worker_task(proxy: _TaskStoreAuthorizationProxy, task_id: str, *, principal: str = "anonymous", request_id: str = "", run_id: str = "", lease_timeout_s: int = 900):
    record = _require_authorized_task(proxy, task_id)
    effective_principal = current_gateway_principal() if proxy._auth_enabled else principal
    if not effective_principal:
        raise HTTPException(status_code=403, detail="Recovery requires an authenticated principal")
    return proxy._store.recover_stale_worker_task(
        task_id,
        principal=effective_principal,
        request_id=request_id,
        run_id=run_id,
        lease_timeout_s=lease_timeout_s,
    )


def _reassign_task(proxy: _TaskStoreAuthorizationProxy, task_id: str, *, worker_id: str, principal: str = "anonymous", request_id: str = "", run_id: str = ""):
    _require_authorized_task(proxy, task_id)
    if proxy._auth_enabled and not proxy._is_system_or_admin():
        raise HTTPException(status_code=403, detail="Task reassignment requires system or admin authorization")
    effective_principal = current_gateway_principal() if proxy._auth_enabled else principal
    return proxy._store.reassign_task(
        task_id,
        worker_id=worker_id,
        principal=effective_principal,
        request_id=request_id,
        run_id=run_id,
    )


_TaskStoreAuthorizationProxy.create_child_task = _create_child_task
_TaskStoreAuthorizationProxy.recover_stale_worker_task = _recover_stale_worker_task
_TaskStoreAuthorizationProxy.reassign_task = _reassign_task
