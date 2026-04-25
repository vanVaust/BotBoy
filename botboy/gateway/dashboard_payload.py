from __future__ import annotations

from typing import Any, Callable, Optional


EMPTY_GRAPH = {"available": False, "nodes": [], "edges": [], "summary": {}}
EMPTY_WORKERS = {"available": True, "worker_count": 5, "registry": []}
EMPTY_HANDOFFS = {"available": False, "summary": {}, "workers": [], "blockers": []}


def _coerce_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _coerce_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _ensure_control_center_contract(payload: dict[str, Any]) -> dict[str, Any]:
    contract = _coerce_dict(payload.get("control_center_contract"))
    payload["control_center_contract"] = contract
    contract.setdefault("contract_version", "ops-legacy")
    contract.setdefault("segment_order", ["operator_surface", "queue_lease", "replay", "incident"])
    write_set = _coerce_dict(contract.get("write_set"))
    write_set.setdefault("dashboard_support", [])
    write_set.setdefault("gateway_dashboard_payload", [])
    contract["write_set"] = write_set
    segments = _coerce_dict(contract.get("segments"))
    queue_lease = _coerce_dict(segments.get("queue_lease"))
    queue_lease.setdefault("snapshot", {})
    queue_lease.setdefault("runtime", {"available": False, "source": "gateway_dashboard_payload"})
    incident = _coerce_dict(segments.get("incident"))
    incident.setdefault("snapshot", {})
    incident.setdefault("runtime", {"available": False, "source": "gateway_dashboard_payload"})
    segments["queue_lease"] = queue_lease
    segments["incident"] = incident
    segments.setdefault("operator_surface", {})
    segments.setdefault("replay", {})
    contract["segments"] = segments
    return contract


def _update_runtime_segments(
    payload: dict[str, Any],
    *,
    metrics: dict[str, Any],
    tasks: dict[str, Any],
) -> None:
    contract = _ensure_control_center_contract(payload)
    segments = _coerce_dict(contract.get("segments"))
    queue_lease = _coerce_dict(segments.get("queue_lease"))
    incident = _coerce_dict(segments.get("incident"))

    queue_runtime = {
        "handoff_queue_depth": _coerce_int(metrics.get("handoff_queue_depth", tasks.get("handoff_queue_depth"))),
        "queued_count": _coerce_int(metrics.get("queued_count", tasks.get("queued_count"))),
        "running_count": _coerce_int(metrics.get("running_count", tasks.get("running_count"))),
        "delegated_count": _coerce_int(metrics.get("delegated_count", tasks.get("delegated_count"))),
        "worker_count": _coerce_int(metrics.get("worker_count", tasks.get("worker_count"))),
        "oldest_running_age_s": _coerce_int(metrics.get("oldest_running_age_s", tasks.get("oldest_running_age_s"))),
        "oldest_blocked_age_s": _coerce_int(metrics.get("oldest_blocked_age_s", tasks.get("oldest_blocked_age_s"))),
        "source": "gateway_dashboard_payload",
        "available": True,
    }
    incident_runtime = {
        "blocked_count": _coerce_int(metrics.get("blocked_count", tasks.get("blocked_count"))),
        "recent_blocker_count": len(_coerce_list(metrics.get("recent_blockers"))),
        "blocker_count": len(_coerce_list(metrics.get("blockers"))),
        "open_incident_count": _coerce_int(metrics.get("blocked_count", tasks.get("blocked_count"))),
        "status": "attention" if _coerce_int(metrics.get("blocked_count", tasks.get("blocked_count"))) > 0 else "watch",
        "source": "gateway_dashboard_payload",
        "available": True,
    }

    queue_lease["runtime"] = queue_runtime
    incident["runtime"] = incident_runtime
    segments["queue_lease"] = queue_lease
    segments["incident"] = incident
    contract["segments"] = segments
    payload["control_center_contract"] = contract


def enrich_dashboard_payload(
    payload: dict[str, Any],
    *,
    store,
    metrics: Optional[dict[str, Any]] = None,
    workers: Optional[dict[str, Any]] = None,
    get_task: Callable[[str], Any],
    task_records_by_root: Callable[[Any, str], list[Any]],
    direct_child_records: Callable[[list[Any], str], list[Any]],
    decorate_task_record: Callable[[Any, Any, Optional[list[Any]]], dict[str, Any]],
    build_task_graph: Callable[[Any, list[Any], str], dict[str, Any]],
) -> dict[str, Any]:
    _ensure_control_center_contract(payload)
    if not store:
        payload.setdefault("workers", EMPTY_WORKERS.copy())
        payload.setdefault("handoffs", EMPTY_HANDOFFS.copy())
        return payload

    metrics = metrics or {}
    workers = _coerce_dict(workers) or EMPTY_WORKERS.copy()
    tasks = _coerce_dict(payload.get("tasks"))
    payload["tasks"] = tasks
    operations_summary = _coerce_dict(payload.get("operations_summary"))
    payload["operations_summary"] = operations_summary
    latest_task = _coerce_dict(tasks.get("latest"))
    latest_task_id = tasks.get("latest_task_id") or latest_task.get("task_id", "")
    latest_record = get_task(latest_task_id) if latest_task_id else None
    latest_records = task_records_by_root(store, latest_record.root_task_id) if latest_record else []

    def decorate_dashboard_task(task: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(task, dict):
            return task
        task_id = str(task.get("task_id", "") or "").strip()
        if not task_id:
            return task
        record = latest_record if latest_record and latest_record.task_id == task_id else get_task(task_id)
        if not record:
            return task
        root_records = (
            latest_records
            if latest_record and record.root_task_id == latest_record.root_task_id
            else task_records_by_root(store, record.root_task_id)
        )
        return decorate_task_record(store, record, root_records)

    latest_graph = build_task_graph(store, latest_records, latest_task_id) if latest_record else EMPTY_GRAPH.copy()
    latest_children = (
        [decorate_task_record(store, child, latest_records) for child in direct_child_records(latest_records, latest_task_id)]
        if latest_record
        else []
    )
    tasks.update(
        {
            "blocked_count": _coerce_int(metrics.get("blocked_count")),
            "delegated_count": _coerce_int(metrics.get("delegated_count")),
            "running_count": _coerce_int(metrics.get("running_count")),
            "queued_count": _coerce_int(metrics.get("queued_count")),
            "worker_count": _coerce_int(metrics.get("worker_count")),
            "handoff_queue_depth": _coerce_int(metrics.get("handoff_queue_depth")),
            "oldest_blocked_age_s": _coerce_int(metrics.get("oldest_blocked_age_s")),
            "oldest_running_age_s": _coerce_int(metrics.get("oldest_running_age_s")),
            "by_worker": _coerce_dict(metrics.get("by_worker")),
            "by_blocked_kind": _coerce_dict(metrics.get("by_blocked_kind")),
            "blockers": _coerce_list(metrics.get("blockers")),
            "recent_blockers": _coerce_list(metrics.get("recent_blockers")),
            "workers": _coerce_list(workers.get("registry")),
            "recent": [decorate_dashboard_task(task) for task in _coerce_list(tasks.get("recent"))],
            "waiting_approval": [decorate_dashboard_task(task) for task in _coerce_list(tasks.get("waiting_approval"))],
            "blocked": [decorate_dashboard_task(task) for task in _coerce_list(tasks.get("blocked"))],
            "delegated": [decorate_dashboard_task(task) for task in _coerce_list(tasks.get("delegated"))],
            "latest": decorate_dashboard_task(latest_task),
            "latest_task": decorate_dashboard_task(latest_task),
            "children": latest_children,
            "graph": latest_graph,
        }
    )
    operations_summary.update(
        {
            "task_total": tasks.get("total", operations_summary.get("task_total", 0)),
            "active_task_count": tasks.get("active_count", operations_summary.get("active_task_count", 0)),
            "waiting_approval_count": tasks.get("waiting_approval_count", operations_summary.get("waiting_approval_count", 0)),
            "failed_task_count": tasks.get("failed_count", operations_summary.get("failed_task_count", 0)),
            "merge_review_ready_count": tasks.get("merge_review_ready_count", operations_summary.get("merge_review_ready_count", 0)),
            "merge_conflict_task_count": tasks.get("merge_conflict_task_count", operations_summary.get("merge_conflict_task_count", 0)),
            "merge_conflict_total": tasks.get("merge_conflict_total", operations_summary.get("merge_conflict_total", 0)),
            "merge_actionable_count": tasks.get("merge_actionable_count", operations_summary.get("merge_actionable_count", 0)),
            "merge_override_task_count": tasks.get("merge_override_task_count", operations_summary.get("merge_override_task_count", 0)),
            "merge_override_total": tasks.get("merge_override_total", operations_summary.get("merge_override_total", 0)),
            "merge_resolution_policies": tasks.get("merge_resolution_policies", operations_summary.get("merge_resolution_policies", {})),
            "latest_merge_task_id": tasks.get("latest_merge_task_id", operations_summary.get("latest_merge_task_id", "")),
            "latest_merge_resolution_policy": tasks.get(
                "latest_merge_resolution_policy",
                operations_summary.get("latest_merge_resolution_policy", ""),
            ),
            "latest_task_id": tasks.get("latest_task_id", operations_summary.get("latest_task_id", "")),
            "blocked_count": _coerce_int(metrics.get("blocked_count")),
            "delegated_count": _coerce_int(metrics.get("delegated_count")),
            "running_count": _coerce_int(metrics.get("running_count")),
            "queued_count": _coerce_int(metrics.get("queued_count")),
            "worker_count": _coerce_int(metrics.get("worker_count")),
            "handoff_queue_depth": _coerce_int(metrics.get("handoff_queue_depth")),
            "oldest_blocked_age_s": _coerce_int(metrics.get("oldest_blocked_age_s")),
            "oldest_running_age_s": _coerce_int(metrics.get("oldest_running_age_s")),
        }
    )
    payload["workers"] = workers
    payload["handoffs"] = {
        "available": True,
        "summary": metrics,
        "workers": _coerce_list(workers.get("registry")),
        "blockers": _coerce_list(metrics.get("blockers")),
        "graph": latest_graph,
        "children": latest_children,
    }
    _update_runtime_segments(payload, metrics=metrics, tasks=tasks)
    return payload
