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


def _secure_worker_child_creation(original):
    """Wrap raw WorkerHandoffService child creation at the gateway boundary.

    Some internal handoff paths receive a raw TaskStore rather than the
    gateway authorization proxy. When gateway authentication is active, the
    parent task remains the authority for principal/tenant identity and the
    caller cannot substitute a different principal through the handoff API.
    """
    def _wrapped(self, *, parent_task, worker, delegated_command, principal, child_request_id, attempt_count):
        store = self.task_store
        if store is None:
            return original(
                self,
                parent_task=parent_task,
                worker=worker,
                delegated_command=delegated_command,
                principal=principal,
                child_request_id=child_request_id,
                attempt_count=attempt_count,
            )

        parent = store.get_task(parent_task.task_id)
        if parent is None:
            raise HTTPException(status_code=404, detail="Task not found")

        auth_enabled = bool(getattr(getattr(self, "bot", None), "config", None)) and bool(
            getattr(getattr(getattr(self, "bot", None), "config", None), "security", None)
            and getattr(getattr(self.bot.config, "security", None), "enable_auth", False)
        )
        gateway_principal = current_gateway_principal()
        gateway_org = current_gateway_org()
        parent_org = str(getattr(parent, "org_id", "default") or "default")

        if auth_enabled:
            if not gateway_principal:
                raise HTTPException(status_code=403, detail="Worker handoff requires an authenticated principal")
            if parent_org != gateway_org:
                raise HTTPException(status_code=404, detail="Task not found")
            if str(getattr(parent, "principal", "")) != gateway_principal and "admin" not in {
                role.lower() for role in getattr(__import__("botboy.gateway.app_context", fromlist=["current_gateway_roles"]), "current_gateway_roles")()
            } and "system" not in {
                role.lower() for role in getattr(__import__("botboy.gateway.app_context", fromlist=["current_gateway_roles"]), "current_gateway_roles")()
            }:
                raise HTTPException(status_code=404, detail="Task not found")

        security = TaskSecurityStore(store).load(parent.task_id)
        effective_principal = str(
            security.principal_id if security is not None else getattr(parent, "principal", "anonymous")
        )
        effective_org = str(
            security.org_id if security is not None else getattr(parent, "org_id", "default") or "default"
        )

        child = original(
            self,
            parent_task=parent_task,
            worker=worker,
            delegated_command=delegated_command,
            principal=effective_principal,
            child_request_id=child_request_id,
            attempt_count=attempt_count,
        )
        if str(getattr(child, "org_id", "default") or "default") != effective_org:
            child = store.update_task(child.task_id, org_id=effective_org).to_context()

        child_security = replace(
            security,
            task_id=child.task_id,
            parent_task_id=parent.task_id,
            approval_id="",
            approval_scope=frozenset(),
            approval_expires_at=None,
        ) if security is not None else SecurityContext.from_legacy(
            principal=effective_principal,
            org_id=effective_org,
            roles=[],
            request_id=str(getattr(child, "request_id", "") or ""),
            task_id=child.task_id,
            parent_task_id=parent.task_id,
            auth_source="task_parent",
        )
        TaskSecurityStore(store).save(child_security)
        return child

    return _wrapped


_TaskStoreAuthorizationProxy.create_child_task = _create_child_task
_TaskStoreAuthorizationProxy.recover_stale_worker_task = _recover_stale_worker_task
_TaskStoreAuthorizationProxy.reassign_task = _reassign_task

try:
    from botboy.worker_handoff_service import WorkerHandoffService

    WorkerHandoffService._build_child_approval_context = _build_child_approval_context
    WorkerHandoffService._create_worker_child_task = _secure_worker_child_creation(
        WorkerHandoffService._create_worker_child_task
    )
except ImportError:
    pass
