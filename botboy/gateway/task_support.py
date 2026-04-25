"""Shared task/worker payload helpers for FastAPI and stdlib gateways."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from botboy.tasks import (
    ACTIVE_TASK_STATUSES,
    TASK_STATUS_BLOCKED,
    TASK_STATUS_CANCELLED,
    TASK_STATUS_COMPLETED,
    TASK_STATUS_FAILED,
    TASK_STATUS_QUEUED,
    TASK_STATUS_RUNNING,
    TASK_STATUS_WAITING_APPROVAL,
)


def worker_registry() -> list[dict[str, Any]]:
    return [
        {
            "worker_id": "planner",
            "display_name": "Planner",
            "role": "strategy",
            "capabilities": ["task decomposition", "handoff planning", "priority shaping"],
            "max_concurrency": 2,
            "approval_profile": "standard",
        },
        {
            "worker_id": "researcher",
            "display_name": "Researcher",
            "role": "analysis",
            "capabilities": ["fact finding", "source validation", "context synthesis"],
            "max_concurrency": 3,
            "approval_profile": "read-only",
        },
        {
            "worker_id": "executor",
            "display_name": "Executor",
            "role": "delivery",
            "capabilities": ["implementation", "patch execution", "workflow follow-through"],
            "max_concurrency": 2,
            "approval_profile": "standard",
        },
        {
            "worker_id": "reviewer",
            "display_name": "Reviewer",
            "role": "quality",
            "capabilities": ["contract checking", "regression analysis", "release gating"],
            "max_concurrency": 4,
            "approval_profile": "approval_required",
        },
        {
            "worker_id": "designer",
            "display_name": "Designer",
            "role": "experience",
            "capabilities": ["control-center design", "information hierarchy", "ui polish"],
            "max_concurrency": 2,
            "approval_profile": "read-only",
        },
    ]


def worker_lookup() -> dict[str, dict[str, Any]]:
    return {worker["worker_id"]: worker for worker in worker_registry()}


def worker_id_from_owner(owner: str) -> str:
    if owner.startswith("worker:"):
        return owner.split(":", 1)[1]
    return ""


def parse_timestamp(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def seconds_since(value: str) -> int:
    parsed = parse_timestamp(value)
    if not parsed:
        return 0
    return max(0, int((datetime.now(timezone.utc) - parsed).total_seconds()))


def lease_expires_at(value: str, seconds: int = 900) -> str:
    parsed = parse_timestamp(value)
    if not parsed:
        return ""
    return (parsed + timedelta(seconds=seconds)).isoformat()


def collect_task_records(store, root_task_id: str = "") -> list[Any]:
    records: list[Any] = []
    offset = 0
    while True:
        kwargs: dict[str, Any] = {"limit": 200, "offset": offset}
        if root_task_id:
            kwargs["root_task_id"] = root_task_id
        batch, total = store.list_tasks(**kwargs)
        records.extend(batch)
        offset += len(batch)
        if not batch or offset >= total:
            break
    return records


def task_records_by_root(store, root_task_id: str) -> list[Any]:
    if not root_task_id:
        return collect_task_records(store)
    return collect_task_records(store, root_task_id=root_task_id)


def children_index(records: list[Any]) -> dict[str, list[Any]]:
    index: dict[str, list[Any]] = {}
    for record in records:
        index.setdefault(record.parent_task_id or "", []).append(record)
    return index


def direct_child_records(records: list[Any], parent_task_id: str) -> list[Any]:
    return [record for record in records if record.parent_task_id == parent_task_id]


def merge_action_catalog(merge_payload: dict[str, Any]) -> list[str]:
    actions = list(merge_payload.get("available_review_actions", []))
    for action in ("resolve_many", "clear_many", "resolve_all_by_source", "apply_preset"):
        if action not in actions:
            actions.append(action)
    return actions


def decorate_merge_payload(merge_payload: dict[str, Any]) -> dict[str, Any]:
    payload = dict(merge_payload or {})
    if payload.get("available"):
        payload["available_review_actions"] = merge_action_catalog(payload)
    return payload


def task_matches_filters(
    record: Any,
    *,
    status: Optional[str],
    principal: Optional[str],
    request_id: Optional[str],
    root_task_id: Optional[str],
) -> bool:
    if status and record.status != status:
        return False
    if principal and record.principal != principal:
        return False
    if request_id and record.request_id != request_id:
        return False
    if root_task_id and record.root_task_id != root_task_id:
        return False
    return True


def task_matches_merge_review(
    bot,
    record: Any,
    merge_review: str,
    *,
    root_records: Optional[list[Any]] = None,
) -> bool:
    del root_records
    normalized = str(merge_review or "").strip().lower().replace("-", "_")
    if not normalized or normalized in {"all", "any", "*"}:
        return True
    merge = decorate_merge_payload(bot.get_task_merge_payload(record.task_id, record=record))
    if not merge.get("available"):
        return False
    review_status = str(merge.get("review_status", "") or "").strip().lower()
    review_state = str(merge.get("review_state", "") or review_status).strip().lower()
    pending_keys = [str(item).strip() for item in merge.get("review_pending_keys", []) if str(item).strip()]
    pending_child_ids = [str(item).strip() for item in merge.get("pending_child_ids", []) if str(item).strip()]
    actionable = bool(merge.get("available")) or bool(merge.get("actionable")) or bool(pending_keys) or bool(pending_child_ids)
    override_active = bool(merge.get("override_active")) or int(merge.get("override_count", 0) or 0) > 0
    if normalized in {"actionable", "pending", "needs_attention"}:
        return actionable
    if normalized == "clean":
        return review_state == "clean" and not actionable
    if normalized == "overridden":
        return review_state == "overridden" or override_active
    if normalized in {"override_active", "overrides"}:
        return override_active
    if normalized in {"reviewed", "available"}:
        return bool(merge.get("available"))
    return review_state == normalized or review_status == normalized


def task_list_records(
    bot,
    store,
    *,
    limit: int,
    offset: int,
    status: Optional[str],
    principal: Optional[str],
    request_id: Optional[str],
    root_task_id: Optional[str],
    merge_review: str = "",
) -> tuple[list[Any], int, list[Any]]:
    if not merge_review:
        records, total = store.list_tasks(
            limit=limit,
            offset=offset,
            status=status,
            principal=principal,
            request_id=request_id,
            root_task_id=root_task_id,
        )
        root_records = task_records_by_root(store, root_task_id) if root_task_id else collect_task_records(store)
        return records, total, root_records
    all_records = collect_task_records(store, root_task_id=root_task_id) if root_task_id else collect_task_records(store)
    filtered = [
        record
        for record in all_records
        if task_matches_filters(
            record,
            status=status,
            principal=principal,
            request_id=request_id,
            root_task_id=root_task_id,
        )
        and task_matches_merge_review(bot, record, merge_review, root_records=all_records)
    ]
    total = len(filtered)
    start = max(offset, 0)
    page = filtered[start : start + limit]
    return page, total, all_records


def decorate_task_record(bot, store, record, *, root_records: Optional[list[Any]] = None) -> dict[str, Any]:
    task = record.to_dict()
    root_records = root_records if root_records is not None else task_records_by_root(store, record.root_task_id)
    current_children_index = children_index(root_records)
    children = current_children_index.get(record.task_id, [])
    worker_id = worker_id_from_owner(record.owner)
    blocking_child = next(
        (child for child in children if child.status in {TASK_STATUS_WAITING_APPROVAL, TASK_STATUS_BLOCKED}),
        None,
    )
    active_children = [child for child in children if child.status in ACTIVE_TASK_STATUSES]
    child_count = len(children)
    delegation_status = "none"
    if worker_id:
        if record.status == TASK_STATUS_RUNNING:
            delegation_status = "leased"
        elif record.status == TASK_STATUS_QUEUED:
            delegation_status = "delegated"
        elif record.status in {TASK_STATUS_WAITING_APPROVAL, TASK_STATUS_BLOCKED}:
            delegation_status = "blocked_on_child"
        elif record.parent_task_id and record.status in {TASK_STATUS_COMPLETED, TASK_STATUS_FAILED, TASK_STATUS_CANCELLED}:
            delegation_status = "awaiting_merge"
    elif child_count and (active_children or blocking_child):
        delegation_status = "blocked_on_child"
    task.update(
        {
            "delegation_status": delegation_status,
            "delegated_to_worker": worker_id,
            "blocked_by_task_id": blocking_child.task_id if blocking_child else "",
            "blocked_kind": blocking_child.status if blocking_child else ("child_active" if active_children else ""),
            "blocked_reason": blocking_child.summary if blocking_child else ("child task is active" if active_children else ""),
            "lease_expires_at": lease_expires_at(record.updated_at) if worker_id and record.status == TASK_STATUS_RUNNING else "",
            "heartbeat_at": record.updated_at if worker_id and record.status in {TASK_STATUS_RUNNING, TASK_STATUS_QUEUED} else "",
            "attempt_count": 1 if record.run_id else 0,
            "retry_after_s": 300 if record.status in {TASK_STATUS_WAITING_APPROVAL, TASK_STATUS_BLOCKED} else 0,
            "is_child": bool(record.parent_task_id),
            "child_count": child_count,
        }
    )
    merge_payload = decorate_merge_payload(bot.get_task_merge_payload(record.task_id, record=record))
    task["merge"] = {}
    if merge_payload.get("available"):
        resolution = merge_payload.get("merge_resolution", {})
        task["merge"] = {
            "available": True,
            "merge_policy": merge_payload.get("merge_policy", ""),
            "configured_resolution_policy": merge_payload.get("configured_resolution_policy", ""),
            "configured_resolution_overrides": merge_payload.get("configured_resolution_overrides", {}),
            "resolution_policy": merge_payload.get("resolution_policy", ""),
            "review_status": merge_payload.get("review_status", ""),
            "review_pending_keys": list(merge_payload.get("review_pending_keys", [])),
            "applied_override_keys": list(merge_payload.get("applied_override_keys", [])),
            "override_count": int(merge_payload.get("override_count", 0) or 0),
            "available_review_actions": list(merge_payload.get("available_review_actions", [])),
            "merged_from_child_task_id": merge_payload.get("merged_from_child_task_id", ""),
            "merged_from_owner": merge_payload.get("merged_from_owner", ""),
            "merged_from_worker": merge_payload.get("merged_from_worker", ""),
            "child_status": merge_payload.get("child_status", ""),
            "child_summary": merge_payload.get("child_summary", ""),
            "linked_artifact_count": int(merge_payload.get("linked_artifact_count", 0) or 0),
            "completed_child_count": int(merge_payload.get("completed_child_count", 0) or 0),
            "active_child_count": int(merge_payload.get("active_child_count", 0) or 0),
            "workers_involved": list(merge_payload.get("workers_involved", [])),
            "conflict_count": int(resolution.get("conflict_count", 0) or 0),
            "resolved_keys": list(resolution.get("resolved_keys", [])),
            "linked_artifacts": [
                {
                    "artifact_id": artifact.get("artifact_id", ""),
                    "task_id": artifact.get("task_id", ""),
                    "category": artifact.get("category", ""),
                    "label": artifact.get("label", ""),
                    "file_path": artifact.get("file_path", ""),
                    "media_type": artifact.get("media_type", ""),
                    "sha256": artifact.get("sha256", ""),
                }
                for artifact in merge_payload.get("linked_artifacts", [])
                if isinstance(artifact, dict)
            ],
        }
    return task


def build_task_graph(bot, store, records: list[Any], focus_task_id: str) -> dict[str, Any]:
    if not records:
        task = store.get_task(focus_task_id)
        if task:
            records = [task]
    current_children_index = children_index(records)
    nodes = [decorate_task_record(bot, store, record, root_records=records) for record in records]
    edges = []
    for record in records:
        for child in current_children_index.get(record.task_id, []):
            edges.append(
                {
                    "from": record.task_id,
                    "to": child.task_id,
                    "kind": child.kind,
                    "owner": child.owner,
                    "status": child.status,
                }
            )
    focus = next((node for node in nodes if node["task_id"] == focus_task_id), None)
    blockers = [
        node
        for node in nodes
        if node["status"] in {TASK_STATUS_WAITING_APPROVAL, TASK_STATUS_BLOCKED}
        or node["blocked_by_task_id"]
    ]
    return {
        "available": True,
        "focus_task_id": focus_task_id,
        "root_task_id": focus["root_task_id"] if focus else "",
        "nodes": nodes,
        "edges": edges,
        "summary": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "child_count": len(current_children_index.get(focus_task_id, [])),
            "blocker_count": len(blockers),
            "worker_count": len({node["delegated_to_worker"] for node in nodes if node["delegated_to_worker"]}),
        },
    }


def task_metrics(bot, store) -> dict[str, Any]:
    records = collect_task_records(store)
    by_worker: dict[str, int] = {}
    by_blocked_kind: dict[str, int] = {}
    blocked_records: list[Any] = []
    running_records: list[Any] = []
    queued_records: list[Any] = []
    delegated_count = 0
    for record in records:
        current_worker_id = worker_id_from_owner(record.owner)
        if current_worker_id:
            by_worker[current_worker_id] = by_worker.get(current_worker_id, 0) + 1
            delegated_count += 1
            if record.status == TASK_STATUS_RUNNING:
                running_records.append(record)
            elif record.status == TASK_STATUS_QUEUED:
                queued_records.append(record)
        if record.status in {TASK_STATUS_WAITING_APPROVAL, TASK_STATUS_BLOCKED}:
            blocked_records.append(record)
            by_blocked_kind[record.status] = by_blocked_kind.get(record.status, 0) + 1
    handoff_queue_depth = sum(
        1
        for record in records
        if worker_id_from_owner(record.owner) and record.status == TASK_STATUS_QUEUED
    )
    return {
        "available": True,
        "blocked_count": len(blocked_records),
        "delegated_count": delegated_count,
        "running_count": len(running_records),
        "queued_count": len(queued_records),
        "worker_count": len(worker_registry()),
        "handoff_queue_depth": handoff_queue_depth,
        "oldest_blocked_age_s": max((seconds_since(record.updated_at) for record in blocked_records), default=0),
        "oldest_running_age_s": max((seconds_since(record.updated_at) for record in running_records), default=0),
        "by_worker": by_worker,
        "by_blocked_kind": by_blocked_kind,
        "blockers": [decorate_task_record(bot, store, record) for record in blocked_records[:10]],
        "recent_blockers": [decorate_task_record(bot, store, record) for record in blocked_records[:5]],
    }


def task_workers_payload(bot, store) -> dict[str, Any]:
    records = collect_task_records(store)
    summaries: list[dict[str, Any]] = []
    for worker in worker_registry():
        current_worker_id = worker["worker_id"]
        worker_records = [record for record in records if worker_id_from_owner(record.owner) == current_worker_id]
        active_records = [record for record in worker_records if record.status in ACTIVE_TASK_STATUSES]
        blocked_records = [record for record in worker_records if record.status in {TASK_STATUS_WAITING_APPROVAL, TASK_STATUS_BLOCKED}]
        summaries.append(
            {
                **worker,
                "summary": {
                    "task_count": len(worker_records),
                    "active_count": len(active_records),
                    "blocked_count": len(blocked_records),
                    "queued_count": sum(1 for record in worker_records if record.status == TASK_STATUS_QUEUED),
                    "running_count": sum(1 for record in worker_records if record.status == TASK_STATUS_RUNNING),
                    "latest_task_id": worker_records[0].task_id if worker_records else "",
                },
                "tasks": [decorate_task_record(bot, store, record, root_records=records) for record in worker_records[:5]],
                "capability_text": ", ".join(worker.get("capabilities", [])),
            }
        )
    return {
        "available": True,
        "worker_count": len(worker_lookup()),
        "registry": summaries,
        "summary": task_metrics(bot, store),
    }
