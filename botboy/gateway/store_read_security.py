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

    cls.list_children = list_children
    cls.get_children = get_children
    cls.get_blockers = get_blockers
    cls.get_blocked_tasks = get_blocked_tasks
    cls._store_read_security_installed = True


install()
