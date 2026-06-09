from __future__ import annotations

import unittest

from botboy.gateway.simple_task_write_handlers import (
    handle_task_cancel,
    handle_task_merge_action,
    handle_task_reassign,
    handle_task_resume,
    handle_worker_node_drain,
    handle_worker_node_heartbeat,
    handle_worker_node_register,
)


class _FakeTask:
    def __init__(
        self,
        task_id: str,
        *,
        status: str = "queued",
        command: str = "status",
        parent_task_id: str = "root-1",
        owner: str = "worker:planner",
    ) -> None:
        self.task_id = task_id
        self.status = status
        self.command = command
        self.parent_task_id = parent_task_id
        self.owner = owner
        self.root_task_id = "root-1"
        self.principal = "tester"
        self.request_id = "req-1"
        self.run_id = "run-1"

    def to_context(self) -> dict:
        return {"task_id": self.task_id}

    def to_dict(self) -> dict:
        return {"task_id": self.task_id, "status": self.status}


class _FakeStore:
    def __init__(self, task: _FakeTask | None = None) -> None:
        self.task = task or _FakeTask("task-1")
        self.reassigned_to = ""
        self.cancelled = ""
        self.node = {
            "node_id": "node-1",
            "worker_id": "planner",
            "queue_name": "planner.node-1",
            "effective_status": "ready",
            "health": "unknown",
        }

    def get_task(self, task_id: str):
        if self.task and self.task.task_id == task_id:
            return self.task
        return None

    def reassign_task(self, task_id: str, *, worker_id: str, principal: str, request_id: str, run_id: str):
        self.reassigned_to = worker_id
        self.task.owner = f"worker:{worker_id}"
        return self.task

    def cancel_task(self, task_id: str, *, principal: str, request_id: str, reason: str):
        self.cancelled = task_id
        self.task.status = "cancelled"
        return self.task

    def register_worker_node(self, **kwargs):
        self.node = {
            "node_id": kwargs["node_id"],
            "worker_id": kwargs["worker_id"],
            "queue_name": f"{kwargs['worker_id']}.{kwargs['node_id']}",
            "effective_status": "ready",
            "health": "unknown",
        }
        return dict(self.node)

    def heartbeat_worker_node(self, node_id: str, **kwargs):
        if node_id != self.node["node_id"]:
            return None
        self.node["effective_status"] = kwargs.get("node_status", "ready")
        self.node["health"] = kwargs.get("health", "healthy") or "healthy"
        return dict(self.node)

    def drain_worker_node(self, node_id: str, **kwargs):
        if node_id != self.node["node_id"]:
            return None
        self.node["effective_status"] = "draining"
        return dict(self.node)

    def worker_node_summary(self):
        return {"node_count": 1, "healthy_count": 1, "draining_count": int(self.node["effective_status"] == "draining")}


class _FakeBot:
    def __init__(self, task: _FakeTask) -> None:
        self.task = task
        self.calls: list[dict] = []

    def apply_task_merge_review_action(self, task_id: str, **kwargs):
        self.calls.append({"task_id": task_id, **kwargs})
        return self.task

    def get_task_merge_payload(self, task_id: str, *, record=None):
        return {"available": True, "configured_resolution_overrides": {}}

    async def process_command(self, command: str, **kwargs):
        self.calls.append({"command": command, **kwargs})
        return {"success": True, "output": "ok"}


class _FakeHandler:
    def __init__(self, *, task: _FakeTask | None = None, roles: list[str] | None = None) -> None:
        active_task = task or _FakeTask("task-1")
        self.store = _FakeStore(active_task)
        self.botboy = _FakeBot(active_task)
        self.auth_enabled = False
        self.request_id = "req-123"
        self.roles = roles or ["admin"]
        self.json_payloads: list[tuple[dict, int]] = []

    def _json(self, payload: dict, status: int = 200) -> None:
        self.json_payloads.append((payload, status))

    def _ensure_access(self, require_auth: bool = False):
        return "tester"

    def _ensure_access_with_roles(self, require_auth: bool = False):
        return "tester", self.roles

    def _task_store(self, optional: bool = False):
        return self.store

    def _worker_lookup(self):
        return {"planner": {"worker_id": "planner"}, "reviewer": {"worker_id": "reviewer"}}

    def _task_records_by_root(self, store, root_task_id: str):
        return [self.store.task]

    def _decorate_task_record(self, store, record, root_records=None):
        return {"task_id": record.task_id, "status": record.status, "owner": record.owner}

    def _decorate_merge_payload(self, payload: dict):
        return {**payload, "available_review_actions": ["resolve"]}

    def _approval_context(self, payload=None, roles=None) -> dict:
        return {"granted": True}

    def _client_identity(self) -> str:
        return "127.0.0.1"


class SimpleTaskWriteHandlersTest(unittest.TestCase):
    def test_handle_task_merge_action(self) -> None:
        handler = _FakeHandler()
        handle_task_merge_action(handler, "task-1", {"action": "refresh"})
        payload, status = handler.json_payloads[-1]
        self.assertEqual(status, 200)
        self.assertEqual(payload["action"], "refresh")
        self.assertEqual(handler.botboy.calls[0]["task_id"], "task-1")

    def test_handle_task_reassign_uses_store_contract(self) -> None:
        handler = _FakeHandler(task=_FakeTask("task-1", status="queued"))
        handle_task_reassign(handler, "task-1", {"worker_id": "reviewer"})
        payload, status = handler.json_payloads[-1]
        self.assertEqual(status, 200)
        self.assertEqual(handler.store.reassigned_to, "reviewer")
        self.assertEqual(payload["worker_id"], "reviewer")

    def test_handle_task_resume(self) -> None:
        handler = _FakeHandler(task=_FakeTask("task-1", status="blocked", command="status"))
        handle_task_resume(handler, "task-1", {})
        payload, status = handler.json_payloads[-1]
        self.assertEqual(status, 200)
        self.assertTrue(payload["success"])
        self.assertEqual(handler.botboy.calls[0]["command"], "status")

    def test_handle_task_cancel(self) -> None:
        handler = _FakeHandler(task=_FakeTask("task-1", status="running"))
        handle_task_cancel(handler, "task-1", {})
        payload, status = handler.json_payloads[-1]
        self.assertEqual(status, 200)
        self.assertEqual(handler.store.cancelled, "task-1")
        self.assertEqual(payload["cancelled"], "task-1")

    def test_handle_worker_node_register(self) -> None:
        handler = _FakeHandler()
        handle_worker_node_register(handler, {"node_id": "node-2", "worker_id": "reviewer"})
        payload, status = handler.json_payloads[-1]
        self.assertEqual(status, 200)
        self.assertTrue(payload["registered"])
        self.assertEqual(payload["node"]["node_id"], "node-2")

    def test_handle_worker_node_heartbeat(self) -> None:
        handler = _FakeHandler()
        handler.store.register_worker_node(node_id="node-2", worker_id="reviewer")
        handle_worker_node_heartbeat(handler, {"node_id": "node-2", "status": "running", "health": "healthy"})
        payload, status = handler.json_payloads[-1]
        self.assertEqual(status, 200)
        self.assertTrue(payload["heartbeat"])
        self.assertEqual(payload["node"]["health"], "healthy")

    def test_handle_worker_node_drain(self) -> None:
        handler = _FakeHandler()
        handler.store.register_worker_node(node_id="node-2", worker_id="reviewer")
        handle_worker_node_drain(handler, "node-2", {"reason": "maintenance"})
        payload, status = handler.json_payloads[-1]
        self.assertEqual(status, 200)
        self.assertTrue(payload["drained"])
        self.assertEqual(payload["node"]["effective_status"], "draining")


if __name__ == "__main__":
    unittest.main()
