"""Defense-in-depth authorization for raw task-store methods exposed by the gateway proxy."""

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
    return _system() or "admin" in _roles()


def _org() -> str:
    return str(current_gateway_org() or "default")


def _visible_task(proxy: Any, task_id: str) -> Any:
    task = proxy._store.get_task(str(task_id or ""))
    if task is None or not proxy._authorized(task):
        return None
    return task


def _visible_metadata(metadata: Any) -> bool:
    if not isinstance(metadata, dict):
        return False
    return _system() or str(metadata.get("org_id", "") or "") == _org()


def _install() -> None:
    cls = _TaskStoreAuthorizationProxy
    if getattr(cls, "_task_boundary_installed", False):
        return

    original_cancel = cls.cancel_task

    def get_queue_lease(self, lease_id: str):
        lease = self._store.get_queue_lease(lease_id)
        if not self._auth_enabled or lease is None or _system():
            return lease
        metadata = lease.get("metadata") or {}
        if not _visible_metadata(metadata):
            return None
        if not _admin() and str(metadata.get("principal", "") or "") != current_gateway_principal():
            return None
        return lease

    def get_worker_node(self, node_id: str):
        node = self._store.get_worker_node(node_id)
        if not self._auth_enabled or node is None or _system():
            return node
        return node if _visible_metadata(node.get("metadata") or {}) else None

    def get_execution_queue(self, queue_name: str):
        queue = self._store.get_execution_queue(queue_name)
        if not self._auth_enabled or queue is None or _system():
            return queue
        return queue if _visible_metadata(queue.get("metadata") or {}) else None

    def list_worker_leases(self, *args, **kwargs):
        records = self._store.list_worker_leases(*args, **kwargs)
        if not self._auth_enabled or _system():
            return records
        visible = []
        for lease in records:
            task = self._store.get_task(str(lease.get("task_id", "") or ""))
            if task is not None and self._authorized(task):
                visible.append(lease)
        return visible

    def worker_summary(self, *args, **kwargs):
        if not self._auth_enabled or _system():
            return self._store.worker_summary(*args, **kwargs)
        tasks, _ = self._store.list_tasks(limit=5000, principal=current_gateway_principal())
        tasks = [task for task in tasks if str(getattr(task, "org_id", "default") or "default") == _org()]
        by_worker: dict[str, int] = {}
        active_by_worker: dict[str, int] = {}
        for task in tasks:
            worker = str(task.delegated_to_worker or "")
            if not worker:
                continue
            by_worker[worker] = by_worker.get(worker, 0) + 1
            if task.status in {"queued", "running"}:
                active_by_worker[worker] = active_by_worker.get(worker, 0) + 1
        base = self._store.worker_summary(*args, **kwargs)
        workers = []
        for profile in base.get("workers", []):
            worker_id = str(profile.get("worker_id", ""))
            workers.append({
                **profile,
                "task_total": by_worker.get(worker_id, 0),
                "active_count": active_by_worker.get(worker_id, 0),
            })
        return {
            **base,
            "by_worker": by_worker,
            "active_by_worker": active_by_worker,
            "workers": workers,
        }

    def list_workers(self, *args, **kwargs):
        return self.worker_summary(*args, **kwargs)["workers"]

    def add_event(self, task_id: str, *args, **kwargs):
        if _visible_task(self, task_id) is None:
            return None
        if self._auth_enabled and not _system():
            kwargs["principal"] = current_gateway_principal() or "anonymous"
        return self._store.add_event(task_id, *args, **kwargs)

    def add_artifact(self, task_id: str, *args, **kwargs):
        if _visible_task(self, task_id) is None:
            return None
        return self._store.add_artifact(task_id, *args, **kwargs)

    def link_artifacts_from_task(self, target_task_id: str, source_task_id: str, *args, **kwargs):
        target = _visible_task(self, target_task_id)
        source = _visible_task(self, source_task_id)
        if target is None or source is None:
            return []
        if not _system() and str(target.org_id) != str(source.org_id):
            return []
        return self._store.link_artifacts_from_task(target_task_id, source_task_id, *args, **kwargs)

    def update_task(self, task_id: str, *args, **kwargs):
        task = _visible_task(self, task_id)
        if task is None:
            return None
        if self._auth_enabled and not _system():
            requested_org = kwargs.get("org_id")
            if requested_org is not None and str(requested_org) != str(task.org_id):
                raise HTTPException(status_code=403, detail="Task tenant cannot be changed")
            kwargs["org_id"] = task.org_id
            kwargs["principal"] = current_gateway_principal() or task.principal
        return self._store.update_task(task_id, *args, **kwargs)

    def start_task(self, task_id: str, *args, **kwargs):
        if _visible_task(self, task_id) is None:
            return None
        if self._auth_enabled and not _system():
            kwargs["principal"] = current_gateway_principal() or "anonymous"
        return self._store.start_task(task_id, *args, **kwargs)

    def finish_task(self, task_id: str, *args, **kwargs):
        if _visible_task(self, task_id) is None:
            return None
        if self._auth_enabled and not _system():
            kwargs["principal"] = current_gateway_principal() or "anonymous"
        return self._store.finish_task(task_id, *args, **kwargs)

    def update_status(self, task_id: str, *args, **kwargs):
        if _visible_task(self, task_id) is None:
            return None
        if self._auth_enabled and not _system():
            kwargs["principal"] = current_gateway_principal() or "anonymous"
        return self._store.update_status(task_id, *args, **kwargs)

    def attach_run(self, task_id: str, *args, **kwargs):
        if _visible_task(self, task_id) is None:
            return None
        return self._store.attach_run(task_id, *args, **kwargs)

    def mark_running(self, task_id: str, *args, **kwargs):
        if _visible_task(self, task_id) is None:
            return None
        if self._auth_enabled and not _system():
            kwargs["principal"] = current_gateway_principal() or "anonymous"
        return self._store.mark_running(task_id, *args, **kwargs)

    def cancel(self, task_id: str, *args, **kwargs):
        if _visible_task(self, task_id) is None:
            return None
        if self._auth_enabled and not _system():
            kwargs["principal"] = current_gateway_principal() or "anonymous"
        return original_cancel(self, task_id, *args, **kwargs)

    cls.get_queue_lease = get_queue_lease
    cls.get_worker_node = get_worker_node
    cls.get_execution_queue = get_execution_queue
    cls.list_worker_leases = list_worker_leases
    cls.worker_summary = worker_summary
    cls.list_workers = list_workers
    cls.add_event = add_event
    cls.add_artifact = add_artifact
    cls.link_artifacts_from_task = link_artifacts_from_task
    cls.update_task = update_task
    cls.start_task = start_task
    cls.finish_task = finish_task
    cls.update_status = update_status
    cls.attach_run = attach_run
    cls.mark_running = mark_running
    cls.cancel_task = cancel
    cls._task_boundary_installed = True


_install()
