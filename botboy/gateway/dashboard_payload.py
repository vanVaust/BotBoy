from __future__ import annotations

from typing import Any, Callable, Optional


EMPTY_GRAPH = {"available": False, "nodes": [], "edges": [], "summary": {}}
EMPTY_WORKERS = {"available": True, "worker_count": 5, "registry": []}
EMPTY_HANDOFFS = {"available": False, "summary": {}, "workers": [], "blockers": []}


def _coerce_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _coerce_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


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
            "blocked_count": metrics["blocked_count"],
            "delegated_count": metrics["delegated_count"],
            "running_count": metrics["running_count"],
            "queued_count": metrics["queued_count"],
            "worker_count": metrics["worker_count"],
            "handoff_queue_depth": metrics["handoff_queue_depth"],
            "oldest_blocked_age_s": metrics["oldest_blocked_age_s"],
            "oldest_running_age_s": metrics["oldest_running_age_s"],
            "by_worker": metrics["by_worker"],
            "by_blocked_kind": metrics["by_blocked_kind"],
            "blockers": metrics["blockers"],
            "recent_blockers": metrics["recent_blockers"],
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
            "blocked_count": metrics["blocked_count"],
            "delegated_count": metrics["delegated_count"],
            "running_count": metrics["running_count"],
            "queued_count": metrics["queued_count"],
            "worker_count": metrics["worker_count"],
            "handoff_queue_depth": metrics["handoff_queue_depth"],
            "oldest_blocked_age_s": metrics["oldest_blocked_age_s"],
            "oldest_running_age_s": metrics["oldest_running_age_s"],
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
    return payload
