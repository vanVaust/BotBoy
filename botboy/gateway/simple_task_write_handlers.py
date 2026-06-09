from __future__ import annotations

import asyncio
from typing import Any, Optional

from botboy.gateway.merge_actions import (
    GatewayMergeActionError,
    build_merge_action_payload as shared_build_merge_action_payload,
)
from botboy.tasks import (
    TASK_STATUS_BLOCKED,
    TASK_STATUS_FAILED,
    TASK_STATUS_QUEUED,
    TASK_STATUS_RUNNING,
    TASK_STATUS_WAITING_APPROVAL,
)


def _run_sync(coro) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, coro)
        return future.result(timeout=30)


def build_task_merge_action_payload(
    handler,
    task_id: str,
    *,
    action: str,
    principal: str = "anonymous",
    request_id: str = "",
    key: str = "",
    source: str = "",
    items: Optional[list[Any]] = None,
    keys: Optional[list[Any]] = None,
    preset: str = "",
) -> dict:
    try:
        return shared_build_merge_action_payload(
            handler.botboy,
            store=handler._task_store(optional=True),
            task_id=task_id,
            action=action,
            principal=principal,
            request_id=request_id,
            key=key,
            source=source,
            items=items,
            keys=keys,
            preset=preset,
            decorate_merge_payload=handler._decorate_merge_payload,
            decorate_task_record=handler._decorate_task_record,
            task_records_by_root=handler._task_records_by_root,
        )
    except GatewayMergeActionError as exc:
        raise ValueError(str(exc)) from exc


def handle_task_merge_action(handler, task_id: str, payload: dict) -> None:
    principal = handler._ensure_access(require_auth=handler.auth_enabled)
    if principal is None:
        return
    store = handler._task_store()
    if not store:
        return
    task = store.get_task(task_id)
    if not task:
        handler._json({"error": "Task not found"}, 404)
        return
    action = str(payload.get("action", "")).strip()
    if not action:
        handler._json({"error": "Missing merge action"}, 400)
        return
    try:
        response = build_task_merge_action_payload(
            handler,
            task_id,
            action=action,
            key=str(payload.get("key", "")).strip(),
            source=str(payload.get("source", "")).strip(),
            items=payload.get("items") if isinstance(payload.get("items"), list) else None,
            keys=payload.get("keys") if isinstance(payload.get("keys"), list) else None,
            preset=str(payload.get("preset", "")).strip(),
            principal=principal,
            request_id=handler.request_id or task.request_id or task_id,
        )
    except ValueError as exc:
        handler._json({"error": str(exc)}, 400)
        return
    handler._json(response)


def handle_task_reassign(handler, task_id: str, payload: dict) -> None:
    principal_id, roles = handler._ensure_access_with_roles(require_auth=handler.auth_enabled)
    if principal_id is None:
        return
    if "admin" not in [role.lower() for role in roles]:
        handler._json({"error": "Admin token required"}, 403)
        return
    store = handler._task_store()
    if not store:
        return
    record = store.get_task(task_id)
    if not record:
        handler._json({"error": "Task not found"}, 404)
        return
    worker_id = str(payload.get("worker_id", "")).strip()
    if not worker_id or worker_id not in handler._worker_lookup():
        handler._json({"error": "Missing or invalid worker_id"}, 400)
        return
    if not record.parent_task_id or not record.owner.startswith("worker:") or record.status not in {TASK_STATUS_QUEUED, TASK_STATUS_RUNNING}:
        handler._json({"error": "Only delegated queued or running child tasks can be reassigned"}, 409)
        return
    updated = store.reassign_task(
        task_id,
        worker_id=worker_id,
        principal=principal_id or record.principal,
        request_id=handler.request_id,
        run_id=record.run_id,
    )
    handler._json(
        {
            "available": True,
            "reassigned": task_id,
            "worker_id": worker_id,
            "task": handler._decorate_task_record(store, updated, root_records=handler._task_records_by_root(store, updated.root_task_id)) if updated else None,
        }
    )


def handle_task_resume(handler, task_id: str, payload: dict) -> None:
    del payload
    bot = handler.botboy
    principal_id, roles = handler._ensure_access_with_roles(require_auth=handler.auth_enabled)
    if principal_id is None:
        return
    store = handler._task_store()
    if not store:
        return
    record = store.get_task(task_id)
    if not record:
        handler._json({"error": "Task not found"}, 404)
        return
    if not record.command.strip():
        handler._json({"error": "Task has no command to resume"}, 409)
        return
    if record.status not in {TASK_STATUS_WAITING_APPROVAL, TASK_STATUS_BLOCKED, TASK_STATUS_FAILED}:
        handler._json({"error": f"Task '{task_id}' is not resumable from status '{record.status}'"}, 409)
        return
    result = _run_sync(
        bot.process_command(
            record.command,
            principal=principal_id or record.principal,
            request_id=handler.request_id or f"{record.request_id or task_id}-resume",
            roles=roles,
            approval_context=handler._approval_context({"approval": True}, roles),
            task_context=record.to_context(),
        )
    )
    handler._json(result)


def handle_task_cancel(handler, task_id: str, payload: dict) -> None:
    del payload
    principal_id, roles = handler._ensure_access_with_roles(require_auth=handler.auth_enabled)
    if principal_id is None:
        return
    del roles
    store = handler._task_store()
    if not store:
        return
    record = store.get_task(task_id)
    if not record:
        handler._json({"error": "Task not found"}, 404)
        return
    cancelled = store.cancel_task(
        task_id,
        principal=principal_id or record.principal,
        request_id=handler.request_id,
        reason="Cancelled via API",
    )
    handler._json(
        {
            "success": True,
            "cancelled": task_id,
            "task": cancelled.to_dict() if cancelled else None,
        }
    )


def handle_worker_node_register(handler, payload: dict) -> None:
    if handler._ensure_access(require_auth=handler.auth_enabled) is None:
        return
    store = handler._task_store()
    if not store:
        return
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
            last_seen_ip=handler._client_identity(),
            node_status=str(payload.get("status", "ready")).strip() or "ready",
        )
    except ValueError as exc:
        handler._json({"error": str(exc)}, 400)
        return
    handler._json(
        {
            "available": True,
            "registered": True,
            "node": node,
            "summary": store.worker_node_summary(),
        }
    )


def handle_worker_node_heartbeat(handler, payload: dict) -> None:
    if handler._ensure_access(require_auth=handler.auth_enabled) is None:
        return
    store = handler._task_store()
    if not store:
        return
    metadata = payload.get("metadata")
    try:
        load_value = float(payload.get("load")) if payload.get("load") is not None else None
    except (TypeError, ValueError):
        handler._json({"error": "Invalid load value"}, 400)
        return
    node = store.heartbeat_worker_node(
        str(payload.get("node_id", "")).strip(),
        node_status=str(payload.get("status", "ready")).strip() or "ready",
        metadata=metadata if isinstance(metadata, dict) else None,
        last_seen_ip=handler._client_identity(),
        health=str(payload.get("health", "")).strip(),
        load=load_value,
    )
    if not node:
        handler._json({"error": "Worker node not found"}, 404)
        return
    handler._json(
        {
            "available": True,
            "heartbeat": True,
            "node": node,
            "summary": store.worker_node_summary(),
        }
    )


def handle_worker_node_drain(handler, node_id: str, payload: dict) -> None:
    if handler._ensure_access(require_auth=handler.auth_enabled) is None:
        return
    store = handler._task_store()
    if not store:
        return
    metadata = payload.get("metadata")
    node = store.drain_worker_node(
        node_id,
        reason=str(payload.get("reason", "")).strip(),
        metadata=metadata if isinstance(metadata, dict) else None,
    )
    if not node:
        handler._json({"error": "Worker node not found"}, 404)
        return
    handler._json(
        {
            "available": True,
            "drained": True,
            "node": node,
            "summary": store.worker_node_summary(),
        }
    )
