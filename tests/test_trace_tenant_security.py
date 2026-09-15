from __future__ import annotations

import unittest
from types import SimpleNamespace

from fastapi import Request

from botboy.gateway.app_context import _current_org, _current_principal, _current_roles
from botboy.gateway.routes_core import create_core_router


class _TraceRecord:
    def __init__(self, run_id: str, principal: str, task_id: str = "") -> None:
        self.run_id = run_id
        self.principal = principal
        self.task_id = task_id

    def to_dict(self) -> dict:
        return {"run_id": self.run_id, "principal": self.principal, "task_id": self.task_id}


class _TraceStore:
    def __init__(self, traces: dict[str, dict]) -> None:
        self.traces = traces

    def list_runs(self, *, limit=20, offset=0, principal=None, request_id=None, status=None):
        records = []
        for trace in self.traces.values():
            run = trace["run"]
            if principal is not None and run["principal"] != principal:
                continue
            records.append(_TraceRecord(run["run_id"], run["principal"], run.get("task_id", "")))
        return records[offset : offset + limit], len(records)

    def get_run(self, run_id: str):
        return self.traces.get(run_id)


class _TaskStore:
    def __init__(self, tasks: dict[str, object]) -> None:
        self.tasks = tasks

    def get_task(self, task_id: str):
        return self.tasks.get(task_id)


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/api/traces", "headers": [], "client": ("127.0.0.1", 1)})


def _endpoint(router, path: str):
    for route in router.routes:
        if getattr(route, "path", None) == path:
            return route.endpoint
    raise AssertionError(f"Route not found: {path}")


class TraceTenantSecurityTest(unittest.IsolatedAsyncioTestCase):
    def _router(self, traces, tasks, principal: str, roles: list[str], org: str):
        def authorize_with_roles(*_args, **_kwargs):
            _current_principal.set(principal)
            _current_roles.set(tuple(roles))
            _current_org.set(org)
            return principal, roles

        ctx = SimpleNamespace(
            bot=SimpleNamespace(trace_store=_TraceStore(traces)),
            auth_enabled=True,
            authorize_with_roles=authorize_with_roles,
            task_store_or_503=lambda: _TaskStore(tasks),
        )
        return create_core_router(ctx)

    async def test_non_admin_cannot_read_other_principal_trace(self):
        traces = {
            "run-b": {"run": {"run_id": "run-b", "principal": "bob", "task_id": ""}, "spans": []}
        }
        router = self._router(traces, {}, "alice", ["user"], "tenant-a")
        endpoint = _endpoint(router, "/api/traces/{run_id}")
        with self.assertRaises(Exception) as raised:
            await endpoint(_request(), "run-b")
        self.assertEqual(getattr(raised.exception, "status_code", None), 404)

    async def test_admin_can_read_same_tenant_trace_via_task_binding(self):
        traces = {
            "run-b": {"run": {"run_id": "run-b", "principal": "bob", "task_id": "task-a"}, "spans": []}
        }
        tasks = {"task-a": SimpleNamespace(org_id="tenant-a")}
        router = self._router(traces, tasks, "alice", ["admin"], "tenant-a")
        endpoint = _endpoint(router, "/api/traces/{run_id}")
        result = await endpoint(_request(), "run-b")
        self.assertEqual(result["run"]["run_id"], "run-b")

    async def test_admin_cannot_read_other_tenant_trace_via_task_binding(self):
        traces = {
            "run-b": {"run": {"run_id": "run-b", "principal": "bob", "task_id": "task-b"}, "spans": []}
        }
        tasks = {"task-b": SimpleNamespace(org_id="tenant-b")}
        router = self._router(traces, tasks, "alice", ["admin"], "tenant-a")
        endpoint = _endpoint(router, "/api/traces/{run_id}")
        with self.assertRaises(Exception) as raised:
            await endpoint(_request(), "run-b")
        self.assertEqual(getattr(raised.exception, "status_code", None), 404)

    async def test_system_can_read_cross_tenant_trace(self):
        traces = {
            "run-b": {"run": {"run_id": "run-b", "principal": "bob", "task_id": "task-b"}, "spans": []}
        }
        tasks = {"task-b": SimpleNamespace(org_id="tenant-b")}
        router = self._router(traces, tasks, "platform", ["system"], "platform")
        endpoint = _endpoint(router, "/api/traces/{run_id}")
        result = await endpoint(_request(), "run-b")
        self.assertEqual(result["run"]["run_id"], "run-b")


if __name__ == "__main__":
    unittest.main()
