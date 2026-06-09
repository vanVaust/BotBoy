from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request

from botboy.gateway.app_context import GatewayAppContext
from botboy.tasks import (
    ACTIVE_TASK_STATUSES,
    TASK_STATUS_BLOCKED,
    TASK_STATUS_FAILED,
    TASK_STATUS_QUEUED,
    TASK_STATUS_RUNNING,
    TASK_STATUS_WAITING_APPROVAL,
)


def create_task_router(ctx: GatewayAppContext) -> APIRouter:
    router = APIRouter()
    bot = ctx.bot

    @router.get("/api/tasks")
    async def tasks_list(
        request: Request,
        limit: int = 20,
        offset: int = 0,
        status: Optional[str] = None,
        principal: Optional[str] = None,
        request_id: Optional[str] = None,
        root_task_id: Optional[str] = None,
        merge_review: Optional[str] = None,
    ):
        ctx.authorize(request.headers, request.client.host if request.client else "", require_auth=ctx.auth_enabled)
        store = ctx.task_store_or_503()
        list_limit = min(limit, 200)
        records, total, root_records = ctx.task_list_records(
            store,
            limit=list_limit,
            offset=max(offset, 0),
            status=status,
            principal=principal,
            request_id=request_id,
            root_task_id=root_task_id,
            merge_review=str(merge_review or ""),
        )
        metrics = ctx.task_metrics(store)
        return {
            "available": True,
            "tasks": [ctx.decorate_task_record(store, record, root_records=root_records) for record in records],
            "total": total,
            "limit": list_limit,
            "offset": max(offset, 0),
            "summary": {**store.summary(), **metrics},
        }

    @router.get("/api/tasks/blockers")
    async def task_blockers(request: Request, limit: int = 50):
        ctx.authorize(request.headers, request.client.host if request.client else "", require_auth=ctx.auth_enabled)
        store = ctx.task_store_or_503()
        metrics = ctx.task_metrics(store)
        blockers = metrics["blockers"][: min(limit, 200)]
        return {
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

    @router.get("/api/tasks/{task_id}/merge")
    async def task_merge(request: Request, task_id: str):
        ctx.authorize(request.headers, request.client.host if request.client else "", require_auth=ctx.auth_enabled)
        return ctx.task_merge_payload(task_id)

    @router.post("/api/tasks/{task_id}/merge/actions")
    async def task_merge_actions(request: Request, task_id: str):
        principal_id, _roles = ctx.authorize_with_roles(
            request.headers,
            request.client.host if request.client else "",
            require_auth=ctx.auth_enabled,
        )
        try:
            payload = await request.json()
        except ValueError:
            payload = {}
        action = str(payload.get("action", "")).strip()
        key = str(payload.get("key", "")).strip()
        source = str(payload.get("source", "")).strip()
        items = payload.get("items")
        keys = payload.get("keys")
        preset = str(payload.get("preset", "")).strip()
        if not action:
            raise HTTPException(status_code=400, detail="Missing merge action")
        return ctx.task_merge_action_payload(
            task_id,
            action=action,
            key=key,
            source=source,
            items=items if isinstance(items, list) else None,
            keys=keys if isinstance(keys, list) else None,
            preset=preset,
            principal=principal_id,
            request_id=getattr(request.state, "request_id", ""),
        )

    @router.get("/api/tasks/{task_id}")
    async def task_detail(request: Request, task_id: str):
        ctx.authorize(request.headers, request.client.host if request.client else "", require_auth=ctx.auth_enabled)
        return ctx.task_detail_payload(task_id)

    @router.get("/api/tasks/{task_id}/children")
    async def task_children(request: Request, task_id: str):
        ctx.authorize(request.headers, request.client.host if request.client else "", require_auth=ctx.auth_enabled)
        store = ctx.task_store_or_503()
        task = store.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        records = ctx.task_records_by_root(store, task.root_task_id)
        children = ctx.direct_child_records(records, task_id)
        return {
            "available": True,
            "task_id": task_id,
            "root_task_id": task.root_task_id,
            "children": [ctx.decorate_task_record(store, child, root_records=records) for child in children],
            "count": len(children),
        }

    @router.get("/api/tasks/{task_id}/graph")
    async def task_graph(request: Request, task_id: str):
        ctx.authorize(request.headers, request.client.host if request.client else "", require_auth=ctx.auth_enabled)
        store = ctx.task_store_or_503()
        task = store.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        records = ctx.task_records_by_root(store, task.root_task_id)
        return ctx.build_task_graph(store, records, task_id)

    @router.get("/api/tasks/{task_id}/events")
    async def task_events(request: Request, task_id: str, limit: int = 100):
        ctx.authorize(request.headers, request.client.host if request.client else "", require_auth=ctx.auth_enabled)
        store = ctx.task_store_or_503()
        task = store.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        events = store.get_events(task_id, limit=min(limit, 200))
        return {"available": True, "task_id": task_id, "events": [event.to_dict() for event in events], "count": len(events)}

    @router.get("/api/workers")
    async def workers_list(request: Request):
        ctx.authorize(request.headers, request.client.host if request.client else "", require_auth=ctx.auth_enabled)
        store = ctx.task_store_or_503()
        return ctx.task_workers_payload(store)

    @router.get("/api/workers/{worker_id}")
    async def worker_detail(request: Request, worker_id: str):
        ctx.authorize(request.headers, request.client.host if request.client else "", require_auth=ctx.auth_enabled)
        store = ctx.task_store_or_503()
        worker = ctx.worker_lookup().get(worker_id)
        if not worker:
            raise HTTPException(status_code=404, detail="Worker not found")
        records = ctx.collect_task_records(store)
        worker_tasks = [record for record in records if ctx.worker_id_from_owner(record.owner) == worker_id]
        decorated = [ctx.decorate_task_record(store, record, root_records=records) for record in worker_tasks]
        active_records = [record for record in worker_tasks if record.status in ACTIVE_TASK_STATUSES]
        blocked_records = [record for record in worker_tasks if record.status in {TASK_STATUS_WAITING_APPROVAL, TASK_STATUS_BLOCKED}]
        return {
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
            "tasks": decorated[:20],
            "task_count": len(worker_tasks),
        }

    @router.get("/api/v2/workers/nodes")
    async def worker_nodes_list(request: Request):
        ctx.authorize(request.headers, request.client.host if request.client else "", require_auth=ctx.auth_enabled)
        store = ctx.task_store_or_503()
        return {
            "available": True,
            "nodes": store.list_worker_nodes(),
            "queues": store.list_execution_queues(),
            "summary": store.worker_node_summary(),
        }

    @router.post("/api/v2/workers/register")
    async def worker_node_register(request: Request):
        ctx.authorize(request.headers, request.client.host if request.client else "", require_auth=ctx.auth_enabled)
        store = ctx.task_store_or_503()
        try:
            payload = await request.json()
        except ValueError:
            payload = {}
        capabilities = payload.get("capabilities")
        metadata = payload.get("metadata")
        try:
            node = store.register_worker_node(
                node_id=str(payload.get("node_id", "")).strip(),
                worker_id=str(payload.get("worker_id", "")).strip(),
                display_name=str(payload.get("display_name", "")).strip(),
                endpoint=str(payload.get("endpoint", "")).strip(),
                capabilities=[str(item).strip() for item in capabilities if str(item).strip()] if isinstance(capabilities, list) else None,
                queue_name=str(payload.get("queue_name", "")).strip(),
                lease_ttl_seconds=int(payload.get("lease_ttl_seconds", 0) or 0),
                max_parallelism=int(payload.get("max_parallelism", 0) or 0),
                max_concurrency=int(payload.get("max_concurrency", 0) or 0),
                metadata=metadata if isinstance(metadata, dict) else None,
                last_seen_ip=request.client.host if request.client else "",
                node_status=str(payload.get("status", "ready")).strip() or "ready",
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "available": True,
            "registered": True,
            "node": node,
            "summary": store.worker_node_summary(),
        }

    @router.post("/api/v2/workers/heartbeat")
    async def worker_node_heartbeat(request: Request):
        ctx.authorize(request.headers, request.client.host if request.client else "", require_auth=ctx.auth_enabled)
        store = ctx.task_store_or_503()
        try:
            payload = await request.json()
        except ValueError:
            payload = {}
        metadata = payload.get("metadata")
        try:
            load_value = float(payload.get("load")) if payload.get("load") is not None else None
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Invalid load value") from exc
        node = store.heartbeat_worker_node(
            str(payload.get("node_id", "")).strip(),
            node_status=str(payload.get("status", "ready")).strip() or "ready",
            metadata=metadata if isinstance(metadata, dict) else None,
            last_seen_ip=request.client.host if request.client else "",
            health=str(payload.get("health", "")).strip(),
            load=load_value,
        )
        if not node:
            raise HTTPException(status_code=404, detail="Worker node not found")
        return {
            "available": True,
            "heartbeat": True,
            "node": node,
            "summary": store.worker_node_summary(),
        }

    @router.post("/api/v2/workers/{node_id}/drain")
    async def worker_node_drain(request: Request, node_id: str):
        ctx.authorize(request.headers, request.client.host if request.client else "", require_auth=ctx.auth_enabled)
        store = ctx.task_store_or_503()
        try:
            payload = await request.json()
        except ValueError:
            payload = {}
        metadata = payload.get("metadata")
        node = store.drain_worker_node(
            node_id,
            reason=str(payload.get("reason", "")).strip(),
            metadata=metadata if isinstance(metadata, dict) else None,
        )
        if not node:
            raise HTTPException(status_code=404, detail="Worker node not found")
        return {
            "available": True,
            "drained": True,
            "node": node,
            "summary": store.worker_node_summary(),
        }

    @router.post("/api/tasks/{task_id}/reassign")
    async def task_reassign(request: Request, task_id: str):
        principal_id, roles = ctx.authorize_with_roles(
            request.headers,
            request.client.host if request.client else "",
            require_auth=ctx.auth_enabled,
        )
        if "admin" not in [role.lower() for role in roles]:
            raise HTTPException(status_code=403, detail="Admin token required")
        store = ctx.task_store_or_503()
        task = store.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        try:
            payload = await request.json()
        except ValueError:
            payload = {}
        worker_id = str(payload.get("worker_id", "")).strip()
        if not worker_id or worker_id not in ctx.worker_lookup():
            raise HTTPException(status_code=400, detail="Missing or invalid worker_id")
        if not task.parent_task_id or not task.owner.startswith("worker:") or task.status not in {TASK_STATUS_QUEUED, TASK_STATUS_RUNNING}:
            raise HTTPException(status_code=409, detail="Only delegated queued or running child tasks can be reassigned")
        updated = store.reassign_task(
            task_id,
            worker_id=worker_id,
            principal=principal_id or task.principal,
            request_id=getattr(request.state, "request_id", ""),
            run_id="",
        )
        return {"available": True, "task": ctx.decorate_task_record(store, updated) if updated else None}

    @router.get("/api/tasks/{task_id}/artifacts")
    async def task_artifacts(request: Request, task_id: str, limit: int = 100):
        ctx.authorize(request.headers, request.client.host if request.client else "", require_auth=ctx.auth_enabled)
        store = ctx.task_store_or_503()
        task = store.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        artifacts = store.get_artifacts(task_id, limit=min(limit, 200))
        return {"available": True, "task_id": task_id, "artifacts": [artifact.to_dict() for artifact in artifacts], "count": len(artifacts)}

    @router.post("/api/tasks/{task_id}/resume")
    async def task_resume(request: Request, task_id: str):
        principal_id, roles = ctx.authorize_with_roles(
            request.headers,
            request.client.host if request.client else "",
            require_auth=ctx.auth_enabled,
        )
        store = ctx.task_store_or_503()
        record = store.get_task(task_id)
        if not record:
            raise HTTPException(status_code=404, detail="Task not found")
        if not record.command.strip():
            raise HTTPException(status_code=409, detail="Task has no command to resume")
        if record.status not in {TASK_STATUS_WAITING_APPROVAL, TASK_STATUS_BLOCKED, TASK_STATUS_FAILED}:
            raise HTTPException(status_code=409, detail=f"Task '{task_id}' is not resumable from status '{record.status}'")
        return await bot.process_command(
            record.command,
            principal=principal_id or record.principal,
            request_id=getattr(request.state, "request_id", "") or f"{record.request_id or task_id}-resume",
            roles=roles,
            approval_context=ctx.approval_context(request.headers, {"approval": True}, roles),
            task_context=record.to_context(),
        )

    @router.post("/api/tasks/{task_id}/cancel")
    async def task_cancel(request: Request, task_id: str):
        principal_id, _roles = ctx.authorize_with_roles(
            request.headers,
            request.client.host if request.client else "",
            require_auth=ctx.auth_enabled,
        )
        store = ctx.task_store_or_503()
        record = store.get_task(task_id)
        if not record:
            raise HTTPException(status_code=404, detail="Task not found")
        cancelled = store.cancel_task(
            task_id,
            principal=principal_id or record.principal,
            request_id=getattr(request.state, "request_id", ""),
            reason="Cancelled via API",
        )
        return {"success": True, "cancelled": task_id, "task": cancelled.to_dict() if cancelled else None}

    return router
