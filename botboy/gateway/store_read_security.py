"""Tenant-safe guards for relationship reads exposed by the gateway task-store proxy."""
from __future__ import annotations

from botboy.gateway.app_context import (
    _TaskStoreAuthorizationProxy,
    current_gateway_roles,
)


def _system() -> bool:
    return "system" in {str(role).lower() for role in current_gateway_roles()}


def install() -> None:
    cls = _TaskStoreAuthorizationProxy
    if getattr(cls, "_store_read_security_installed", False):
        return

    def _visible(self, task_id: str):
        record = self._store.get_task(str(task_id or ""))
        return record if self._authorized(record) else None

    def list_children(self, task_id: str, *, limit: int = 100):
        parent = _visible(self, task_id)
        if parent is None:
            return []
        children = self._store.list_children(parent.task_id, limit=limit)
        if not self._auth_enabled or _system():
            return children
        return [child for child in children if self._authorized(child)]

    def get_children(self, task_id: str, *, limit: int = 100):
        return list_children(self, task_id, limit=limit)

    def get_blockers(self, task_id: str, *, limit: int = 100):
        record = _visible(self, task_id)
        if record is None or not record.blocked_by_task_id:
            return []
        blocker = self._store.get_task(record.blocked_by_task_id)
        if blocker is None or not self._authorized(blocker):
            return []
        return [blocker]

    def get_blocked_tasks(self, task_id: str, *, limit: int = 100):
        record = _visible(self, task_id)
        if record is None:
            return []
        records = self._store.get_blocked_tasks(record.task_id, limit=limit)
        if not self._auth_enabled or _system():
            return records
        return [item for item in records if self._authorized(item)]

    def list_blockers(self, *, limit: int = 100):
        records = self._store.list_blockers(limit=limit)
        if not self._auth_enabled or _system():
            return records
        return [item for item in records if self._authorized(item)]

    def task_graph(self, task_id: str):
        record = _visible(self, task_id)
        if record is None:
            return {"task": None, "children": [], "root_task_id": ""}
        graph = self._store.task_graph(record.task_id)
        if not self._auth_enabled or _system():
            return graph
        tasks = [item for item in graph.get("tasks", []) if self._authorized(self._store.get_task(item.get("task_id", "")))]
        children = [item for item in graph.get("children", []) if self._authorized(self._store.get_task(item.get("task_id", "")))]
        root = graph.get("root")
        if isinstance(root, dict):
            root_record = self._store.get_task(root.get("task_id", ""))
            if root_record is None or not self._authorized(root_record):
                root = None
        graph = dict(graph)
        graph["tasks"] = tasks
        graph["children"] = children
        graph["root"] = root
        return graph

    def list_worker_leases(self, *, limit: int = 100, only_stale: bool = False, lease_timeout_s: int = 900):
        leases = self._store.list_worker_leases(
            limit=limit,
            only_stale=only_stale,
            lease_timeout_s=lease_timeout_s,
        )
        if not self._auth_enabled or _system():
            return leases
        return [lease for lease in leases if self.get_task(str(lease.get("task_id", "") or "")) is not None]

    cls.list_children = list_children
    cls.get_children = get_children
    cls.get_blockers = get_blockers
    cls.get_blocked_tasks = get_blocked_tasks
    cls.list_blockers = list_blockers
    cls.task_graph = task_graph
    cls.list_worker_leases = list_worker_leases
    cls._store_read_security_installed = True


install()
