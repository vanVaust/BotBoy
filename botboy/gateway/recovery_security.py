"""Authorization boundaries for task recovery and child-task operations."""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from fastapi import HTTPException

from botboy.gateway.app_context import (
    _TaskStoreAuthorizationProxy,
    current_gateway_org,
    current_gateway_principal,
    current_gateway_roles,
)
from botboy.security_context import SecurityContext
from botboy.task_security import TaskSecurityStore


_BLOCKED_PROXY_MUTATORS = frozenset(
    {
        "create_task", "create_child_task", "update_task", "start_task", "mark_running",
        "finish_task", "update_status", "attach_run", "cancel_task", "add_event",
        "add_artifact_file", "add_artifact", "write_artifact", "link_artifacts_from_task",
        "recover_stale_worker_task", "reassign_task", "acquire_queue_lease", "renew_queue_lease",
        "release_queue_lease", "register_worker_node", "heartbeat_worker_node", "drain_worker_node",
    }
)


def _security_store(store: Any) -> TaskSecurityStore | None:
    """Return the persisted security store only for SQLite-backed TaskStore implementations."""
    if not hasattr(store, "_get_conn"):
        return None
    return TaskSecurityStore(store)


def _authorized_record(proxy: _TaskStoreAuthorizationProxy, task_id: str) -> Any:
    record = proxy._store.get_task(task_id)
    return record if proxy._authorized(record) else None


def _require_authorized_task(proxy: _TaskStoreAuthorizationProxy, task_id: str) -> Any:
    record = _authorized_record(proxy, task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return record


def _create_child_task(proxy: _TaskStoreAuthorizationProxy, parent_task_id: str, *, worker_id: str, title: str, **kwargs: Any):
    parent = _require_authorized_task(proxy, parent_task_id)
    store = _security_store(proxy._store)
    parent_security = store.load(parent.task_id) if store else None
    principal = str(parent_security.principal_id if parent_security else getattr(parent, "principal", "anonymous"))
    org_id = str(parent_security.org_id if parent_security else getattr(parent, "org_id", "default") or "default")
    kwargs.pop("principal", None)
    kwargs.pop("org_id", None)
    child = proxy._store.create_child_task(parent_task_id, worker_id=worker_id, title=title, principal=principal, **kwargs)
    if str(getattr(child, "org_id", "default") or "default") != org_id:
        child = proxy._store.update_task(child.task_id, org_id=org_id)
    if store:
        if parent_security is not None:
            child_security = replace(parent_security, task_id=child.task_id, parent_task_id=parent.task_id, approval_id="", approval_scope=frozenset(), approval_expires_at=None)
        else:
            child_security = SecurityContext.from_legacy(
                principal=principal, org_id=org_id, roles=list(proxy._roles()),
                request_id=str(getattr(child, "request_id", "") or ""), task_id=child.task_id,
                parent_task_id=parent.task_id, auth_source="task_parent",
            )
        store.save(child_security)
    return child


def _recover_stale_worker_task(proxy: _TaskStoreAuthorizationProxy, task_id: str, *, principal: str = "anonymous", request_id: str = "", run_id: str = "", lease_timeout_s: int = 900):
    if _authorized_record(proxy, task_id) is None:
        return None
    effective_principal = current_gateway_principal() if proxy._auth_enabled else principal
    if not effective_principal:
        raise HTTPException(status_code=403, detail="Recovery requires an authenticated principal")
    return proxy._store.recover_stale_worker_task(task_id, principal=effective_principal, request_id=request_id, run_id=run_id, lease_timeout_s=lease_timeout_s)


def _reassign_task(proxy: _TaskStoreAuthorizationProxy, task_id: str, *, worker_id: str, principal: str = "anonymous", request_id: str = "", run_id: str = ""):
    _require_authorized_task(proxy, task_id)
    if proxy._auth_enabled and not proxy._is_system_or_admin():
        raise HTTPException(status_code=403, detail="Task reassignment requires system or admin authorization")
    effective_principal = current_gateway_principal() if proxy._auth_enabled else principal
    return proxy._store.reassign_task(task_id, worker_id=worker_id, principal=effective_principal, request_id=request_id, run_id=run_id)


def _build_child_approval_context(self, approval_context):
    context = dict(approval_context or {})
    is_server_approval = bool(
        context.get("source") == "approval_store"
        and str(context.get("approval_id", "")).strip()
        and context.get("approval_scope") == "task.execute"
    )
    if is_server_approval:
        return {"granted": False, "explicit": False, "source": "worker_handoff", "reason": "child_requires_independent_approval"}
    inherited_scope = bool(context.get("approval_scope")) or (bool(context.get("granted")) and bool(context.get("explicit")))
    context["granted"] = inherited_scope
    if inherited_scope and "approval_scope" not in context:
        context["approval_scope"] = "parent_inherited"
    context.setdefault("source", "worker_handoff")
    context.setdefault("reason", "child_execution")
    return context


def _secure_worker_child_creation(original):
    """Guard raw WorkerHandoffService child creation at the gateway boundary."""
    def _wrapped(self, *, parent_task, worker, delegated_command, principal, child_request_id, attempt_count):
        store = self.task_store
        if store is None:
            return original(self, parent_task=parent_task, worker=worker, delegated_command=delegated_command, principal=principal, child_request_id=child_request_id, attempt_count=attempt_count)
        parent = store.get_task(parent_task.task_id)
        if parent is None:
            raise HTTPException(status_code=404, detail="Task not found")
        config = getattr(getattr(self, "bot", None), "config", None)
        security = getattr(config, "security", None)
        auth_enabled = bool(getattr(security, "enable_auth", False))
        roles = {role.lower() for role in current_gateway_roles()}
        gateway_principal = current_gateway_principal()
        gateway_org = current_gateway_org()
        parent_org = str(getattr(parent, "org_id", "default") or "default")
        if auth_enabled:
            if not gateway_principal:
                raise HTTPException(status_code=403, detail="Worker handoff requires an authenticated principal")
            if parent_org != gateway_org:
                raise HTTPException(status_code=404, detail="Task not found")
            if str(getattr(parent, "principal", "")) != gateway_principal and not (roles & {"admin", "system"}):
                raise HTTPException(status_code=404, detail="Task not found")
        security_store = _security_store(store)
        parent_security = security_store.load(parent.task_id) if security_store else None
        effective_principal = str(parent_security.principal_id if parent_security is not None else getattr(parent, "principal", "anonymous"))
        effective_org = str(parent_security.org_id if parent_security is not None else getattr(parent, "org_id", "default") or "default")
        child = original(self, parent_task=parent_task, worker=worker, delegated_command=delegated_command, principal=effective_principal, child_request_id=child_request_id, attempt_count=attempt_count)
        child_record = store.get_task(child.task_id)
        if child_record is not None and str(getattr(child_record, "org_id", "default") or "default") != effective_org:
            child = store.update_task(child.task_id, org_id=effective_org).to_context()
        if security_store:
            child_security = (
                replace(parent_security, task_id=child.task_id, parent_task_id=parent_task.task_id, approval_id="", approval_scope=frozenset(), approval_expires_at=None)
                if parent_security is not None
                else SecurityContext.from_legacy(principal=effective_principal, org_id=effective_org, roles=[], request_id=str(getattr(child, "request_id", "") or ""), task_id=child.task_id, parent_task_id=parent_task.task_id, auth_source="task_parent")
            )
            security_store.save(child_security)
        return child
    return _wrapped


def _secure_proxy_attribute_access(original):
    def _guarded(proxy, name: str):
        if proxy._auth_enabled and name in _BLOCKED_PROXY_MUTATORS:
            raise HTTPException(status_code=403, detail=f"Direct task-store mutation '{name}' is not authorized through the gateway proxy")
        return original(proxy, name)
    return _guarded


def _blocked_update_task(proxy: _TaskStoreAuthorizationProxy, *args: Any, **kwargs: Any):
    """Explicitly bind the raw task updater to the same deny-by-default boundary."""
    if proxy._auth_enabled:
        raise HTTPException(status_code=403, detail="Direct task-store mutation 'update_task' is not authorized through the gateway proxy")
    return proxy._store.update_task(*args, **kwargs)


_TaskStoreAuthorizationProxy.create_child_task = _create_child_task
_TaskStoreAuthorizationProxy.recover_stale_worker_task = _recover_stale_worker_task
_TaskStoreAuthorizationProxy.reassign_task = _reassign_task
_TaskStoreAuthorizationProxy.update_task = _blocked_update_task
_TaskStoreAuthorizationProxy.__getattr__ = _secure_proxy_attribute_access(_TaskStoreAuthorizationProxy.__getattr__)

try:
    from botboy.worker_handoff_service import WorkerHandoffService
    WorkerHandoffService._build_child_approval_context = _build_child_approval_context
    WorkerHandoffService._create_worker_child_task = _secure_worker_child_creation(WorkerHandoffService._create_worker_child_task)
except ImportError:
    pass
