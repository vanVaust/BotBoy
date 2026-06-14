from __future__ import annotations

from typing import Any

from botboy.tasks import (
    ACTIVE_TASK_STATUSES,
    TASK_STATUS_BLOCKED,
    TASK_STATUS_QUEUED,
    TASK_STATUS_RUNNING,
    TASK_STATUS_WAITING_APPROVAL,
)


def handle_traces_get(handler, params: dict) -> None:
    bot = handler.botboy
    if not (bot and bot.trace_store):
        handler._json({"error": "Trace store not available"}, 503)
        return
    if handler._ensure_access(require_auth=handler.auth_enabled) is None:
        return
    limit = int(params.get("limit", ["20"])[0])
    offset = int(params.get("offset", ["0"])[0])
    principal = params.get("principal", [None])[0]
    request_id = params.get("request_id", [None])[0]
    status = params.get("status", [None])[0]
    runs, total = bot.trace_store.list_runs(
        limit=min(limit, 200),
        offset=offset,
        principal=principal,
        request_id=request_id,
        status=status,
    )
    handler._json({"runs": [run.to_dict() for run in runs], "total": total, "limit": limit, "offset": offset})


def handle_trace_detail(handler, run_id: str) -> None:
    bot = handler.botboy
    if not (bot and bot.trace_store):
        handler._json({"error": "Trace store not available"}, 503)
        return
    if handler._ensure_access(require_auth=handler.auth_enabled) is None:
        return
    trace = bot.trace_store.get_run(run_id)
    if not trace:
        handler._json({"error": "Trace run not found"}, 404)
        return
    handler._json(trace)


def handle_tasks_get(handler, params: dict) -> None:
    if handler._ensure_access(require_auth=handler.auth_enabled) is None:
        return
    store = handler._task_store()
    if not store:
        return
    limit = int(params.get("limit", ["20"])[0])
    offset = int(params.get("offset", ["0"])[0])
    status = params.get("status", [None])[0]
    principal = params.get("principal", [None])[0]
    request_id = params.get("request_id", [None])[0]
    root_task_id = params.get("root_task_id", [None])[0]
    merge_review = params.get("merge_review", [None])[0]
    list_limit = min(limit, 200)
    tasks, total, root_records = handler._task_list_records(
        store,
        limit=list_limit,
        offset=max(offset, 0),
        status=status,
        principal=principal,
        request_id=request_id,
        root_task_id=root_task_id,
        merge_review=str(merge_review or ""),
    )
    metrics = handler._task_metrics(store)
    handler._json(
        {
            "available": True,
            "tasks": [handler._decorate_task_record(store, task, root_records=root_records) for task in tasks],
            "total": total,
            "limit": list_limit,
            "offset": max(offset, 0),
            "summary": {**store.summary(), **metrics},
        }
    )


def handle_task_detail(handler, task_id: str) -> None:
    if handler._ensure_access(require_auth=handler.auth_enabled) is None:
        return
    store = handler._task_store()
    if not store:
        return
    task = store.get_task(task_id)
    if not task:
        handler._json({"error": "Task not found"}, 404)
        return
    root_records = handler._task_records_by_root(store, task.root_task_id)
    children = handler._direct_child_records(root_records, task_id)
    handler._json(
        {
            "available": True,
            "task": handler._decorate_task_record(store, task, root_records=root_records),
            "events": [event.to_dict() for event in store.get_events(task_id)],
            "artifacts": [artifact.to_dict() for artifact in store.get_artifacts(task_id)],
            "children": [handler._decorate_task_record(store, child, root_records=root_records) for child in children],
            "graph": handler._build_task_graph(store, root_records, task_id),
        }
    )


def handle_task_merge(handler, task_id: str) -> None:
    if handler._ensure_access(require_auth=handler.auth_enabled) is None:
        return
    store = handler._task_store()
    if not store:
        return
    task = store.get_task(task_id)
    if not task:
        handler._json({"error": "Task not found"}, 404)
        return
    merge = handler._decorate_merge_payload(handler.botboy.get_task_merge_payload(task_id, record=task))
    handler._json(
        {
            "available": bool(merge.get("available", False)),
            "task_id": task.task_id,
            "root_task_id": task.root_task_id,
            "task_status": task.status,
            "merge": merge,
        }
    )


def handle_task_children(handler, task_id: str) -> None:
    if handler._ensure_access(require_auth=handler.auth_enabled) is None:
        return
    store = handler._task_store()
    if not store:
        return
    task = store.get_task(task_id)
    if not task:
        handler._json({"error": "Task not found"}, 404)
        return
    root_records = handler._task_records_by_root(store, task.root_task_id)
    children = handler._direct_child_records(root_records, task_id)
    handler._json(
        {
            "available": True,
            "task_id": task_id,
            "root_task_id": task.root_task_id,
            "children": [handler._decorate_task_record(store, child, root_records=root_records) for child in children],
            "count": len(children),
        }
    )


def handle_task_graph(handler, task_id: str) -> None:
    if handler._ensure_access(require_auth=handler.auth_enabled) is None:
        return
    store = handler._task_store()
    if not store:
        return
    task = store.get_task(task_id)
    if not task:
        handler._json({"error": "Task not found"}, 404)
        return
    root_records = handler._task_records_by_root(store, task.root_task_id)
    handler._json(handler._build_task_graph(store, root_records, task_id))


def handle_task_events(handler, task_id: str) -> None:
    if handler._ensure_access(require_auth=handler.auth_enabled) is None:
        return
    store = handler._task_store()
    if not store:
        return
    if not store.get_task(task_id):
        handler._json({"error": "Task not found"}, 404)
        return
    events = store.get_events(task_id)
    handler._json(
        {
            "available": True,
            "task_id": task_id,
            "events": [event.to_dict() for event in events],
            "count": len(events),
        }
    )


def handle_task_blockers(handler, params: dict) -> None:
    if handler._ensure_access(require_auth=handler.auth_enabled) is None:
        return
    store = handler._task_store()
    if not store:
        return
    limit = int(params.get("limit", ["50"])[0])
    metrics = handler._task_metrics(store)
    blockers = metrics["blockers"][: min(limit, 200)]
    handler._json(
        {
            "available": True,
            "total": len(metrics["blockers"]),
            "limit": min(limit, 200),
            "blockers": blockers,
            "summary": {
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
            },
        }
    )


def handle_workers_get(handler, params: dict) -> None:
    del params
    if handler._ensure_access(require_auth=handler.auth_enabled) is None:
        return
    store = handler._task_store()
    if not store:
        return
    handler._json(handler._task_workers_payload(store))


def handle_worker_nodes_get(handler, params: dict) -> None:
    del params
    if handler._ensure_access(require_auth=handler.auth_enabled) is None:
        return
    store = handler._task_store()
    if not store:
        return
    handler._json(
        {
            "available": True,
            "nodes": store.list_worker_nodes(),
            "queues": store.list_execution_queues(),
            "summary": store.worker_node_summary(),
        }
    )


def handle_worker_leases_get(handler, params: dict) -> None:
    if handler._ensure_access(require_auth=handler.auth_enabled) is None:
        return
    store = handler._task_store()
    if not store:
        return
    queue_name = str((params.get("queue_name") or [""])[0] or "").strip()
    node_id = str((params.get("node_id") or [""])[0] or "").strip()
    task_id = str((params.get("task_id") or [""])[0] or "").strip()
    include_released = str((params.get("include_released") or ["false"])[0]).lower() in {"1", "true", "yes"}
    include_expired = str((params.get("include_expired") or ["true"])[0]).lower() not in {"0", "false", "no"}
    try:
        limit = max(1, min(int((params.get("limit") or ["100"])[0] or 100), 500))
    except (TypeError, ValueError):
        handler._json({"error": "Invalid limit"}, 400)
        return
    handler._json(
        {
            "available": True,
            "leases": store.list_queue_leases(
                queue_name=queue_name,
                node_id=node_id,
                task_id=task_id,
                include_released=include_released,
                include_expired=include_expired,
                limit=limit,
            ),
            "summary": store.queue_summary(),
        }
    )


def handle_worker_detail(handler, worker_id: str) -> None:
    if handler._ensure_access(require_auth=handler.auth_enabled) is None:
        return
    store = handler._task_store()
    if not store:
        return
    worker = handler._worker_lookup().get(worker_id)
    if not worker:
        handler._json({"error": "Worker not found"}, 404)
        return
    records = handler._collect_task_records(store)
    worker_tasks = [record for record in records if handler._worker_id_from_owner(record.owner) == worker_id]
    active_records = [record for record in worker_tasks if record.status in ACTIVE_TASK_STATUSES]
    blocked_records = [record for record in worker_tasks if record.status in {TASK_STATUS_WAITING_APPROVAL, TASK_STATUS_BLOCKED}]
    handler._json(
        {
            "available": True,
            "worker": {
                **worker,
                "summary": {
                    "task_count": len(worker_tasks),
                    "active_count": len(active_records),
                    "blocked_count": len(blocked_records),
                    "queued_count": sum(1 for record in worker_tasks if record.status == TASK_STATUS_QUEUED),
                    "running_count": sum(1 for record in worker_tasks if record.status == TASK_STATUS_RUNNING),
                    "latest_task_id": worker_tasks[0].task_id if worker_tasks else "",
                },
            },
            "tasks": [handler._decorate_task_record(store, record, root_records=records) for record in worker_tasks[:20]],
            "task_count": len(worker_tasks),
        }
    )


def handle_task_artifacts(handler, task_id: str) -> None:
    if handler._ensure_access(require_auth=handler.auth_enabled) is None:
        return
    store = handler._task_store()
    if not store:
        return
    if not store.get_task(task_id):
        handler._json({"error": "Task not found"}, 404)
        return
    artifacts = store.get_artifacts(task_id)
    handler._json(
        {
            "available": True,
            "task_id": task_id,
            "artifacts": [artifact.to_dict() for artifact in artifacts],
            "count": len(artifacts),
        }
    )
