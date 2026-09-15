"""Tenant-safe task summary methods for the authenticated gateway proxy."""
from __future__ import annotations

from collections import Counter
from typing import Any

from botboy.gateway.app_context import _TaskStoreAuthorizationProxy
from botboy.gateway.task_support import worker_registry, worker_id_from_owner
from botboy.tasks import (
    ACTIVE_TASK_STATUSES,
    TASK_STATUS_BLOCKED,
    TASK_STATUS_FAILED,
    TASK_STATUS_QUEUED,
    TASK_STATUS_RUNNING,
    TASK_STATUS_WAITING_APPROVAL,
)


def _records(proxy: _TaskStoreAuthorizationProxy) -> list[Any]:
    records: list[Any] = []
    offset = 0
    while True:
        batch, total = proxy.list_tasks(limit=200, offset=offset)
        records.extend(batch)
        offset += len(batch)
        if not batch or offset >= total:
            break
    return records


def _merge_payload(record: Any) -> dict[str, Any]:
    result = getattr(record, "result", {})
    if not isinstance(result, dict):
        return {}
    if isinstance(result.get("merge_policy"), str) and result.get("merge_policy"):
        return result
    data = result.get("data")
    if isinstance(data, dict) and isinstance(data.get("merge"), dict):
        return data["merge"]
    return {}


def _summary(proxy: _TaskStoreAuthorizationProxy) -> dict[str, Any]:
    records = _records(proxy)
    by_status = Counter(str(getattr(record, "status", "") or "") for record in records)
    blocked = [record for record in records if record.status in {TASK_STATUS_WAITING_APPROVAL, TASK_STATUS_BLOCKED}]
    running = [record for record in records if record.status == TASK_STATUS_RUNNING]
    queued = [record for record in records if record.status == TASK_STATUS_QUEUED]
    delegated = [record for record in records if worker_id_from_owner(str(getattr(record, "owner", "") or ""))]
    by_worker = Counter(worker_id_from_owner(str(getattr(record, "owner", "") or "")) for record in delegated)
    by_worker.pop("", None)
    by_blocked_kind = Counter(
        str(getattr(record, "blocked_kind", "") or "")
        for record in blocked
        if str(getattr(record, "blocked_kind", "") or "")
    )

    merge_ready = merge_conflict_tasks = merge_conflicts = merge_actionable = 0
    merge_override_tasks = merge_overrides = 0
    policies: Counter[str] = Counter()
    latest_merge_task_id = ""
    latest_merge_policy = ""
    for record in records:
        merge = _merge_payload(record)
        if not merge:
            continue
        merge_ready += 1
        policy = str(merge.get("resolution_policy", "") or "").strip()
        if policy:
            policies[policy] += 1
        resolution = merge.get("merge_resolution") or {}
        conflicts = int(resolution.get("conflict_count", 0) or 0)
        if conflicts:
            merge_conflict_tasks += 1
            merge_conflicts += conflicts
        overrides = int(merge.get("override_count", resolution.get("override_count", 0)) or 0)
        if overrides:
            merge_override_tasks += 1
            merge_overrides += overrides
        pending = merge.get("review_pending_keys", resolution.get("pending_conflict_keys", []))
        if isinstance(pending, list) and pending:
            merge_actionable += 1
        if not latest_merge_task_id:
            latest_merge_task_id = str(getattr(record, "task_id", "") or "")
            latest_merge_policy = policy

    leases: list[dict[str, Any]] = []
    try:
        leases = list(proxy.list_queue_leases(include_released=False, include_expired=True, limit=500) or [])
    except (AttributeError, TypeError):
        leases = []
    stale = [lease for lease in leases if lease.get("is_stale")]
    recoverable = [lease for lease in stale if lease.get("is_recoverable")]

    recent = records[:5]
    waiting = [record.to_dict() for record in records if record.status == TASK_STATUS_WAITING_APPROVAL][:5]
    blocked_rows = [record.to_dict() for record in blocked[:5]]
    delegated_rows = [record.to_dict() for record in delegated[:5]]
    latest = recent[0].to_dict() if recent else None
    return {
        "available": True,
        "total": len(records),
        "by_status": dict(by_status),
        "active_count": sum(by_status.get(status, 0) for status in ACTIVE_TASK_STATUSES),
        "waiting_approval_count": by_status.get(TASK_STATUS_WAITING_APPROVAL, 0),
        "failed_count": by_status.get(TASK_STATUS_FAILED, 0),
        "blocked_count": len(blocked),
        "running_count": len(running),
        "queued_count": len(queued),
        "delegated_count": len(delegated),
        "worker_count": len(worker_registry()),
        "handoff_queue_depth": len(queued),
        "stale_lease_count": len(stale),
        "recoverable_lease_count": len(recoverable),
        "merge_review_ready_count": merge_ready,
        "merge_conflict_task_count": merge_conflict_tasks,
        "merge_conflict_total": merge_conflicts,
        "merge_actionable_count": merge_actionable,
        "merge_override_task_count": merge_override_tasks,
        "merge_override_total": merge_overrides,
        "merge_resolution_policies": dict(policies),
        "latest_merge_task_id": latest_merge_task_id,
        "latest_merge_resolution_policy": latest_merge_policy,
        "workers": worker_registry(),
        "recent": [record.to_dict() for record in recent],
        "waiting_approval": waiting,
        "blocked": blocked_rows,
        "delegated": delegated_rows,
        "stale_leases": stale[:5],
        "recoverable_leases": recoverable[:5],
        "by_worker": dict(by_worker),
        "by_blocked_kind": dict(by_blocked_kind),
        "latest": latest,
        "latest_task_id": latest.get("task_id", "") if latest else "",
        "latest_status": latest.get("status", "") if latest else "",
    }


def _worker_summary(proxy: _TaskStoreAuthorizationProxy) -> dict[str, Any]:
    records = _records(proxy)
    summaries = []
    for worker in worker_registry():
        worker_id = worker["worker_id"]
        owned = [record for record in records if worker_id_from_owner(str(getattr(record, "owner", "") or "")) == worker_id]
        summaries.append({
            **worker,
            "summary": {
                "task_count": len(owned),
                "active_count": sum(record.status in ACTIVE_TASK_STATUSES for record in owned),
                "blocked_count": sum(record.status in {TASK_STATUS_WAITING_APPROVAL, TASK_STATUS_BLOCKED} for record in owned),
                "queued_count": sum(record.status == TASK_STATUS_QUEUED for record in owned),
                "running_count": sum(record.status == TASK_STATUS_RUNNING for record in owned),
                "latest_task_id": owned[0].task_id if owned else "",
            },
            "tasks": [record.to_dict() for record in owned[:5]],
            "capability_text": ", ".join(worker.get("capabilities", [])),
        })
    return {"available": True, "worker_count": len(worker_registry()), "registry": summaries, "summary": _summary(proxy)}


_TaskStoreAuthorizationProxy.summary = _summary
_TaskStoreAuthorizationProxy.worker_summary = _worker_summary
