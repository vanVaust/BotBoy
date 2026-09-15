"""Defense-in-depth authorization for shared worker queues and task recovery.

The task-store contains shared queue/task tables, so the gateway must bind
queue, node, lease, recovery, and delegation operations to the server-verified
security context. This module patches the gateway-facing authorization proxy
without changing the underlying persistence schema.
"""

from typing import Any

from fastapi import HTTPException

from botboy.gateway.app_context import (
    _TaskStoreAuthorizationProxy,
    current_gateway_org,
    current_gateway_principal,
    current_gateway_roles,
)
from botboy.security_context import SecurityContext
from botboy.task_security import TaskSecurityStore


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


def _authorized_task(proxy: Any, task_id: str) -> Any:
    task = proxy._store.get_task(task_id)
    if task is None or not proxy._authorized(task):
        return None
    return task


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
    original_recover = getattr(cls, "recover_stale_worker_task", None)
    original_reassign = getattr(cls, "reassign_task", None)
    original_create_child = getattr(cls, "create_child_task", None)

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

    def recover(self, task_id: str, *args, **kwargs):
        if original_recover is None:
            return None
        self._require_worker_control()
        task = _authorized_task(self, task_id)
        if task is None:
            return None
        # Never accept an arbitrary event principal from the caller. The
        # target task's persisted security identity remains authoritative.
        if self._auth_enabled:
            kwargs["principal"] = current_gateway_principal() or str(task.principal)
        return original_recover(self, task_id, *args, **kwargs)

    def reassign(self, task_id: str, *args, **kwargs):
        if original_reassign is None:
            return None
        if not self._auth_enabled:
            return original_reassign(self, task_id, *args, **kwargs)
        self._require_worker_control()
        if not _admin():
            raise HTTPException(status_code=403, detail="Task reassignment requires admin or system authorization")
        task = _authorized_task(self, task_id)
        if task is None:
            return None
        worker_id = str(kwargs.get("worker_id", "") or "").strip()
        if not worker_id and args:
            worker_id = str(args[0] or "").strip()
        if not worker_id:
            raise HTTPException(status_code=400, detail="Missing worker_id")
        kwargs["principal"] = current_gateway_principal() or str(task.principal)
        result = original_reassign(self, task_id, *args, **kwargs)
        # Reassignment changes execution ownership, not tenant/principal
        # authorization. Preserve the authoritative persisted security context.
        security_store = TaskSecurityStore(self._store)
        context = security_store.load(task_id)
        if context is not None:
            security_store.save(context)
        return result

    def create_child(self, parent_task_id: str, *args, **kwargs):
        if original_create_child is None:
            return None
        parent = _authorized_task(self, parent_task_id)
        if parent is None:
            return None
        if self._auth_enabled:
            kwargs["principal"] = str(parent.principal)
        child = original_create_child(self, parent_task_id, *args, **kwargs)
        child_id = getattr(child, "task_id", "") if child is not None else ""
        if not child_id:
            return child
        # create_child_task historically defaulted org_id to "default". Repair
        # that boundary immediately and copy the server-persisted security context.
        updated = self._store.update_task(child_id, org_id=str(parent.org_id or "default"))
        security_store = TaskSecurityStore(self._store)
        parent_context = security_store.load(parent_task_id)
        if parent_context is not None:
            security_store.save(parent_context.with_task(child_id, parent_task_id=parent_task_id))
        return updated.to_context() if updated is not None else child

    def get_children(self, task_id: str, *args, **kwargs):
        if _authorized_task(self, task_id) is None:
            return []
        records = self._store.list_children(task_id, *args, **kwargs)
        return [record for record in records if self._authorized(record)]

    def list_children(self, task_id: str, *args, **kwargs):
        return get_children(self, task_id, *args, **kwargs)

    def get_blockers(self, task_id: str, *args, **kwargs):
        if _authorized_task(self, task_id) is None:
            return []
        records = self._store.get_blockers(task_id, *args, **kwargs)
        return [record for record in records if self._authorized(record)]

    def get_blocked_tasks(self, task_id: str, *args, **kwargs):
        if _authorized_task(self, task_id) is None:
            return []
        records = self._store.get_blocked_tasks(task_id, *args, **kwargs)
        return [record for record in records if self._authorized(record)]

    def list_blockers(self, *args, **kwargs):
        records = self._store.list_blockers(*args, **kwargs)
        if not self._auth_enabled or _system():
            return records
        org = _org()
        return [record for record in records if str(getattr(record, "org_id", "default") or "default") == org and ("admin" in _roles() or str(getattr(record, "principal", "")) == current_gateway_principal())]

    def task_graph(self, task_id: str, *args, **kwargs):
        if _authorized_task(self, task_id) is None:
            return {"task": None, "children": [], "root_task_id": ""}
        graph = self._store.task_graph(task_id, *args, **kwargs)
        if not self._auth_enabled or _system():
            return graph
        org = _org()
        principal = current_gateway_principal()
        visible = lambda value: str(value.get("org_id", "default") or "default") == org and ("admin" in _roles() or str(value.get("principal", "")) == principal)
        for key in ("children", "tasks"):
            graph[key] = [value for value in graph.get(key, []) if visible(value)]
        if graph.get("root") and not visible(graph["root"]):
            graph["root"] = None
        return graph

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
    if original_recover is not None:
        cls.recover_stale_worker_task = recover
    if original_reassign is not None:
        cls.reassign_task = reassign
    if original_create_child is not None:
        cls.create_child_task = create_child
    cls.get_children = get_children
    cls.list_children = list_children
    cls.get_blockers = get_blockers
    cls.get_blocked_tasks = get_blocked_tasks
    cls.list_blockers = list_blockers
    cls.task_graph = task_graph


_install()
