"""Defense-in-depth authorization for shared worker queues and leases.

The task-store contains shared queue tables, so the gateway must bind queue/node/
lease operations to the server-verified tenant and, where applicable, principal.
This module patches the gateway-facing authorization proxy without changing the
underlying persistence schema; tenant identity is carried in trusted metadata.
"""

from typing import Any

from fastapi import HTTPException

from botboy.gateway.app_context import (
    _TaskStoreAuthorizationProxy,
    current_gateway_org,
    current_gateway_principal,
    current_gateway_roles,
)


def _roles() -> set[str]:
    return {str(role).lower() for role in current_gateway_roles()}


def _system() -> bool:
    return "system" in _roles()


def _admin() -> bool:
    return "admin" in _roles() or _system()


def _org() -> str:
    return str(current_gateway_org() or "default")


def _metadata_org(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    return str(value.get("org_id", "") or "").strip()


def _require_same_org(metadata: Any) -> None:
    if not _TaskStoreAuthorizationProxy._auth_enabled_for_patch:
        return
    if _system():
        return
    if _metadata_org(metadata) != _org():
        raise HTTPException(status_code=404, detail="Resource not found")


def _node_org(node: dict) -> str:
    return _metadata_org(node.get("metadata") or {})


def _lease_org(lease: dict) -> str:
    return _metadata_org(lease.get("metadata") or {})


def _install() -> None:
    cls = _TaskStoreAuthorizationProxy
    if getattr(cls, "_queue_security_installed", False):
        return

    original_acquire = cls.acquire_queue_lease
    original_renew = cls.renew_queue_lease
    original_release = cls.release_queue_lease
    original_list_nodes = cls.list_worker_nodes
    original_list_queues = cls.list_execution_queues
    original_list_leases = cls.list_queue_leases
    original_node_summary = cls.worker_node_summary
    original_queue_summary = cls.queue_summary

    def acquire(self, *args, **kwargs):
        self._require_worker_control()
        if not self._auth_enabled:
            return original_acquire(self, *args, **kwargs)
        queue_name = str(kwargs.get("queue_name", "") or "").strip()
        node_id = str(kwargs.get("node_id", "") or "").strip()
        task_id = str(kwargs.get("task_id", "") or "").strip()
        node = self._store.get_worker_node(node_id) if node_id else None
        queue = self._store.get_execution_queue(queue_name) if queue_name else None
        if not node or not queue:
            return None
        _require_same_org(node.get("metadata") or {})
        _require_same_org(queue.get("metadata") or {})
        if str(node.get("queue_name", "") or "") != queue_name:
            return None
        if not _admin():
            principal = current_gateway_principal()
            metadata = node.get("metadata") or {}
            owner = str(metadata.get("owner_principal", "") or "")
            worker_id = str(node.get("worker_id", "") or "")
            if principal not in {owner, worker_id}:
                return None
        if task_id:
            task = self._store.get_task(task_id)
            if task is None or not self._authorized(task):
                return None
        metadata = kwargs.get("metadata")
        merged = dict(metadata) if isinstance(metadata, dict) else {}
        merged["org_id"] = _org()
        merged["principal"] = current_gateway_principal() or "anonymous"
        kwargs["metadata"] = merged
        return original_acquire(self, *args, **kwargs)

    def renew(self, lease_id: str, *args, **kwargs):
        self._require_worker_control()
        lease = self._store.get_queue_lease(lease_id)
        if lease is None:
            return None
        if self._auth_enabled:
            _require_same_org(lease.get("metadata") or {})
            if not _admin():
                owner = str((lease.get("metadata") or {}).get("principal", "") or "")
                if owner != current_gateway_principal():
                    return None
        return original_renew(self, lease_id, *args, **kwargs)

    def release(self, lease_id: str, *args, **kwargs):
        self._require_worker_control()
        lease = self._store.get_queue_lease(lease_id)
        if lease is None:
            return None
        if self._auth_enabled:
            _require_same_org(lease.get("metadata") or {})
            if not _admin():
                owner = str((lease.get("metadata") or {}).get("principal", "") or "")
                if owner != current_gateway_principal():
                    return None
        return original_release(self, lease_id, *args, **kwargs)

    def list_nodes(self, *args, **kwargs):
        records = original_list_nodes(self, *args, **kwargs)
        if not self._auth_enabled or _system():
            return records
        return [node for node in records if _node_org(node) == _org()]

    def list_queues(self, *args, **kwargs):
        records = original_list_queues(self, *args, **kwargs)
        if not self._auth_enabled or _system():
            return records
        return [queue for queue in records if _metadata_org(queue.get("metadata") or {}) == _org()]

    def list_leases(self, *args, **kwargs):
        records = original_list_leases(self, *args, **kwargs)
        if not self._auth_enabled or _system():
            return records
        return [lease for lease in records if _lease_org(lease) == _org()]

    def node_summary(self, *args, **kwargs):
        if not self._auth_enabled or _system():
            return original_node_summary(self, *args, **kwargs)
        nodes = list_nodes(self, *args, **kwargs)
        queues = list_queues(self)
        by_worker: dict[str, int] = {}
        by_status: dict[str, int] = {}
        healthy = stale = draining = 0
        for node in nodes:
            worker = str(node.get("worker_id", "") or "")
            status = str(node.get("effective_status", "") or "unknown")
            by_worker[worker] = by_worker.get(worker, 0) + 1
            by_status[status] = by_status.get(status, 0) + 1
            healthy += bool(node.get("is_healthy"))
            stale += bool(node.get("is_stale"))
            draining += status == "draining"
        leases = list_leases(self, include_released=False, include_expired=True, limit=500)
        return {
            "available": True,
            "node_count": len(nodes),
            "healthy_count": healthy,
            "stale_count": stale,
            "draining_count": draining,
            "queue_count": len(queues),
            "lease_count": len(leases),
            "by_worker": dict(sorted(by_worker.items())),
            "by_effective_status": dict(sorted(by_status.items())),
        }

    def queue_summary(self, *args, **kwargs):
        if not self._auth_enabled or _system():
            return original_queue_summary(self, *args, **kwargs)
        queues = list_queues(self)
        leases = list_leases(self, include_released=True, include_expired=True, limit=500)
        by_queue: dict[str, dict[str, int]] = {}
        by_status: dict[str, int] = {}
        for lease in leases:
            q = str(lease.get("queue_name", "") or "")
            status = str(lease.get("lease_status", "") or "unknown")
            by_status[status] = by_status.get(status, 0) + 1
            bucket = by_queue.setdefault(q, {"lease_count": 0, "active_count": 0, "expired_count": 0, "released_count": 0})
            bucket["lease_count"] += 1
            bucket[f"{status}_count"] = bucket.get(f"{status}_count", 0) + 1
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

    cls.acquire_queue_lease = acquire
    cls.renew_queue_lease = renew
    cls.release_queue_lease = release
    cls.list_worker_nodes = list_nodes
    cls.list_execution_queues = list_queues
    cls.list_queue_leases = list_leases
    cls.worker_node_summary = node_summary
    cls.queue_summary = queue_summary
    cls._auth_enabled_for_patch = True
    cls._queue_security_installed = True


_install()
