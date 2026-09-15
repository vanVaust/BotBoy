"""Tenant-safe worker, queue, and lease reads for authenticated gateway proxies."""
from __future__ import annotations

from typing import Any

from botboy.gateway.app_context import (
    _TaskStoreAuthorizationProxy,
    current_gateway_org,
    current_gateway_principal,
    current_gateway_roles,
)


def _roles() -> set[str]:
    return {str(role).lower() for role in current_gateway_roles()}


def _is_system() -> bool:
    return "system" in _roles()


def _is_admin() -> bool:
    return "admin" in _roles()


def _task_visible(proxy: _TaskStoreAuthorizationProxy, task_id: str) -> bool:
    if not task_id:
        return False
    return proxy.get_task(task_id) is not None


def _lease_visible(proxy: _TaskStoreAuthorizationProxy, lease: dict[str, Any]) -> bool:
    if _is_system():
        return True
    task_id = str(lease.get("task_id", "") or "")
    if task_id:
        return _task_visible(proxy, task_id)
    metadata = lease.get("metadata") or {}
    principal = str(metadata.get("principal", "") or metadata.get("owner_principal", "") or "")
    return bool(principal) and principal == current_gateway_principal()


def _scoped_worker_nodes(proxy: _TaskStoreAuthorizationProxy, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    proxy._require_worker_control()
    nodes = proxy._store.list_worker_nodes(*args, **kwargs)
    if not proxy._auth_enabled or _is_system():
        return nodes
    org_id = current_gateway_org()
    principal = current_gateway_principal()
    scoped: list[dict[str, Any]] = []
    for node in nodes:
        metadata = node.get("metadata") or {}
        node_org = str(metadata.get("org_id", "default") or "default")
        owner = str(metadata.get("owner_principal", "") or "")
        worker_id = str(node.get("worker_id", "") or "")
        node_id = str(node.get("node_id", "") or "")
        if node_org != org_id:
            continue
        if _is_admin() or owner == principal or worker_id == principal or node_id == principal:
            scoped.append(node)
    return scoped


def _scoped_execution_queues(proxy: _TaskStoreAuthorizationProxy, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    proxy._require_worker_control()
    queues = proxy._store.list_execution_queues(*args, **kwargs)
    if not proxy._auth_enabled or _is_system():
        return queues
    scoped_nodes = _scoped_worker_nodes(proxy)
    allowed_queue_names = {
        str(node.get("queue_name", "") or "")
        for node in scoped_nodes
        if str(node.get("queue_name", "") or "")
    }
    leases = proxy._store.list_queue_leases(include_released=False, include_expired=True, limit=500)
    allowed_queue_names.update(
        str(lease.get("queue_name", "") or "")
        for lease in leases
        if _lease_visible(proxy, lease)
    )
    return [queue for queue in queues if str(queue.get("queue_name", "") or "") in allowed_queue_names]


def _scoped_queue_leases(proxy: _TaskStoreAuthorizationProxy, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    proxy._require_worker_control()
    leases = proxy._store.list_queue_leases(*args, **kwargs)
    if not proxy._auth_enabled or _is_system():
        return leases
    return [lease for lease in leases if _lease_visible(proxy, lease)]


def _scoped_worker_node_summary(proxy: _TaskStoreAuthorizationProxy, *args: Any, **kwargs: Any) -> dict[str, Any]:
    proxy._require_worker_control()
    nodes = _scoped_worker_nodes(proxy, *args, **kwargs)
    queues = _scoped_execution_queues(proxy)
    by_worker: dict[str, int] = {}
    by_effective_status: dict[str, int] = {}
    healthy_count = stale_count = draining_count = 0
    for node in nodes:
        worker_id = str(node.get("worker_id", "") or "")
        by_worker[worker_id] = by_worker.get(worker_id, 0) + 1
        status = str(node.get("effective_status", "") or "unknown")
        by_effective_status[status] = by_effective_status.get(status, 0) + 1
        healthy_count += bool(node.get("is_healthy"))
        stale_count += bool(node.get("is_stale"))
        draining_count += node.get("effective_status") == "draining"
    leases = _scoped_queue_leases(proxy, include_released=True, include_expired=True, limit=500)
    return {
        "available": True,
        "node_count": len(nodes),
        "healthy_count": healthy_count,
        "stale_count": stale_count,
        "draining_count": draining_count,
        "queue_count": len(queues),
        "lease_count": len(leases),
        "by_worker": dict(sorted(by_worker.items())),
        "by_effective_status": dict(sorted(by_effective_status.items())),
    }


def _scoped_queue_summary(proxy: _TaskStoreAuthorizationProxy, *args: Any, **kwargs: Any) -> dict[str, Any]:
    proxy._require_worker_control()
    queues = _scoped_execution_queues(proxy)
    leases = _scoped_queue_leases(proxy, include_released=True, include_expired=True, limit=500)
    by_queue: dict[str, dict[str, int]] = {}
    by_status: dict[str, int] = {}
    for lease in leases:
        queue_name = str(lease.get("queue_name", "") or "")
        status = str(lease.get("lease_status", "") or "unknown")
        by_status[status] = by_status.get(status, 0) + 1
        bucket = by_queue.setdefault(queue_name, {"lease_count": 0, "active_count": 0, "expired_count": 0, "released_count": 0})
        bucket["lease_count"] += 1
        if status == "active":
            bucket["active_count"] += 1
        elif status == "expired":
            bucket["expired_count"] += 1
        elif status == "released":
            bucket["released_count"] += 1
    return {
        "available": True,
        "queue_count": len(queues),
        "lease_count": len(leases),
        "active_lease_count": by_status.get("active", 0),
        "expired_lease_count": by_status.get("expired", 0),
        "released_lease_count": by_status.get("released", 0),
        "recoverable_lease_count": sum(1 for lease in leases if lease.get("is_recoverable")),
        "by_status": dict(sorted(by_status.items())),
        "by_queue": dict(sorted(by_queue.items())),
        "queues": queues,
        "recent_leases": leases[:5],
    }


_TaskStoreAuthorizationProxy.list_worker_nodes = _scoped_worker_nodes
_TaskStoreAuthorizationProxy.list_execution_queues = _scoped_execution_queues
_TaskStoreAuthorizationProxy.list_queue_leases = _scoped_queue_leases
_TaskStoreAuthorizationProxy.worker_node_summary = _scoped_worker_node_summary
_TaskStoreAuthorizationProxy.queue_summary = _scoped_queue_summary
