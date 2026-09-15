"""Deny raw TaskStore method fallback through authenticated gateway proxies."""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from botboy.gateway.app_context import (
    _TaskStoreAuthorizationProxy,
    current_gateway_org,
    current_gateway_principal,
    current_gateway_roles,
)


_BLOCKED_RAW_MUTATORS = frozenset(
    {
        "create_task",
        "create_child_task",
        "recover_stale_worker_task",
        "reassign_task",
        "add_artifact_file",
        "write_artifact",
        "acquire_queue_lease",
        "renew_queue_lease",
        "release_queue_lease",
        "register_worker_node",
        "heartbeat_worker_node",
        "drain_worker_node",
    }
)


def _scoped_summary(self: _TaskStoreAuthorizationProxy) -> dict[str, Any]:
    """Return summary data scoped to the current gateway principal and org."""
    if not self._auth_enabled:
        return self._store.summary()

    roles = {str(role).lower() for role in current_gateway_roles()}
    principal = current_gateway_principal()
    org_id = current_gateway_org()
    if not principal and "system" not in roles:
        return {"available": True, "total": 0, "by_status": {}, "recent": [], "waiting_approval": [], "blocked": [], "delegated": [], "latest": None}

    records: list[Any] = []
    offset = 0
    while True:
        kwargs: dict[str, Any] = {"limit": 500, "offset": offset}
        if "system" not in roles and "admin" not in roles:
            kwargs["principal"] = principal
        batch, total = self._store.list_tasks(**kwargs)
        records.extend(
            record for record in batch
            if str(getattr(record, "org_id", "default") or "default") == org_id
        )
        offset += len(batch)
        if not batch or offset >= total:
            break

    records.sort(key=lambda record: str(getattr(record, "created_at", "") or ""), reverse=True)
    by_status: dict[str, int] = {}
    by_worker: dict[str, int] = {}
    by_blocked_kind: dict[str, int] = {}
    for record in records:
        status = str(getattr(record, "status", "") or "")
        by_status[status] = by_status.get(status, 0) + 1
        worker = str(getattr(record, "delegated_to_worker", "") or "")
        if worker:
            by_worker[worker] = by_worker.get(worker, 0) + 1
        blocked_kind = str(getattr(record, "blocked_kind", "") or "")
        if blocked_kind:
            by_blocked_kind[blocked_kind] = by_blocked_kind.get(blocked_kind, 0) + 1

    blocked_statuses = {"waiting_approval", "blocked"}
    latest = records[0].to_dict() if records else None
    return {
        "available": True,
        "total": len(records),
        "by_status": by_status,
        "active_count": sum(by_status.get(status, 0) for status in ("queued", "running", "waiting_approval", "blocked")),
        "waiting_approval_count": by_status.get("waiting_approval", 0),
        "failed_count": by_status.get("failed", 0),
        "blocked_count": sum(1 for record in records if record.status in blocked_statuses or getattr(record, "blocked_by_task_id", "")),
        "running_count": by_status.get("running", 0),
        "queued_count": by_status.get("queued", 0),
        "delegated_count": sum(by_worker.values()),
        "worker_count": len(by_worker),
        "handoff_queue_depth": sum(1 for record in records if getattr(record, "delegation_status", "") == "delegated"),
        "stale_lease_count": 0,
        "recoverable_lease_count": 0,
        "merge_review_ready_count": 0,
        "merge_conflict_task_count": 0,
        "merge_conflict_total": 0,
        "merge_actionable_count": 0,
        "merge_override_task_count": 0,
        "merge_override_total": 0,
        "merge_resolution_policies": {},
        "latest_merge_task_id": "",
        "latest_merge_resolution_policy": "",
        "workers": [],
        "recent": [record.to_dict() for record in records[:5]],
        "waiting_approval": [record.to_dict() for record in records if record.status == "waiting_approval"][:5],
        "blocked": [record.to_dict() for record in records if record.status in blocked_statuses or getattr(record, "blocked_by_task_id", "")][:5],
        "delegated": [record.to_dict() for record in records if getattr(record, "delegated_to_worker", "")][:5],
        "stale_leases": [],
        "recoverable_leases": [],
        "by_worker": by_worker,
        "by_blocked_kind": by_blocked_kind,
        "latest": latest,
        "latest_task_id": latest["task_id"] if latest else "",
        "latest_status": latest["status"] if latest else "",
    }


def _secure_getattr(self: _TaskStoreAuthorizationProxy, name: str) -> Any:
    """Fail closed instead of forwarding unknown TaskStore methods."""
    if self._auth_enabled:
        if name == "summary":
            return lambda *args, **kwargs: _scoped_summary(self)
        if name in _BLOCKED_RAW_MUTATORS:
            raise HTTPException(
                status_code=403,
                detail=f"Task-store mutation '{name}' is not exposed through the authenticated gateway proxy",
            )
        raise AttributeError(
            f"Task-store method '{name}' is not exposed through the authenticated gateway proxy"
        )
    return getattr(self._store, name)


_TaskStoreAuthorizationProxy.__getattr__ = _secure_getattr
