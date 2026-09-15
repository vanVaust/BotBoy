"""Gateway authorization hooks for merge-service reads and mutations."""
from __future__ import annotations

from typing import Any, Callable

from fastapi import HTTPException

from botboy.gateway.app_context import current_gateway_org, current_gateway_principal, current_gateway_roles
from botboy.task_merge_service import TaskMergeService


_PATCHED = False


def _auth_enabled(service: TaskMergeService) -> bool:
    config = getattr(getattr(service, "bot", None), "config", None)
    security = getattr(config, "security", None)
    return bool(getattr(security, "enable_auth", False))


def _authorized_record(service: TaskMergeService, record: Any) -> bool:
    if record is None:
        return False
    if not _auth_enabled(service):
        return True
    roles = {str(role).lower() for role in current_gateway_roles()}
    if "system" in roles:
        return True
    if str(getattr(record, "org_id", "default") or "default") != current_gateway_org():
        return False
    if "admin" in roles:
        return True
    principal = current_gateway_principal()
    return bool(principal) and str(getattr(record, "principal", "")) == principal


def _family_authorized(service: TaskMergeService, task_id: str) -> bool:
    store = getattr(service, "task_store", None)
    if store is None:
        return False
    get_task = getattr(store, "get_task", None)
    task = get_task(task_id) if callable(get_task) else None
    if not _authorized_record(service, task):
        return False

    list_children = getattr(store, "list_children", None)
    if not callable(list_children):
        return True
    pending = [str(task_id)]
    seen: set[str] = set()
    while pending:
        parent_id = pending.pop()
        if parent_id in seen:
            continue
        seen.add(parent_id)
        try:
            children = list_children(parent_id, limit=200) or []
        except (AttributeError, TypeError, ValueError):
            children = []
        for child in children:
            if not _authorized_record(service, child):
                return False
            child_id = str(getattr(child, "task_id", "") or "")
            if child_id:
                pending.append(child_id)
    return True


def _guard_task(service: TaskMergeService, task_id: str) -> None:
    if not _family_authorized(service, str(task_id or "")):
        raise HTTPException(status_code=404, detail="Task not found")


def _wrap_get_payload(original: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    def guarded(self: TaskMergeService, task_id: str, *, record=None) -> dict[str, Any]:
        _guard_task(self, task_id)
        if record is not None and not _authorized_record(self, record):
            raise HTTPException(status_code=404, detail="Task not found")
        return original(self, task_id, record=record)

    return guarded


def _wrap_apply_action(original: Callable[..., Any]) -> Callable[..., Any]:
    def guarded(self: TaskMergeService, task_id: str, **kwargs: Any):
        _guard_task(self, task_id)
        principal = str(kwargs.get("principal", "") or "")
        if _auth_enabled(self) and "system" not in {str(role).lower() for role in current_gateway_roles()}:
            current_principal = current_gateway_principal()
            if principal and principal != current_principal:
                kwargs["principal"] = current_principal
        return original(self, task_id, **kwargs)

    return guarded


def install() -> None:
    global _PATCHED
    if _PATCHED:
        return
    TaskMergeService.get_task_merge_payload = _wrap_get_payload(TaskMergeService.get_task_merge_payload)
    TaskMergeService.apply_task_merge_review_action = _wrap_apply_action(TaskMergeService.apply_task_merge_review_action)
    _PATCHED = True


install()
