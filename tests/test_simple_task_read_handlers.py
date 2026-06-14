from __future__ import annotations

import unittest

from botboy.gateway.simple_task_read_handlers import (
    handle_task_artifacts,
    handle_task_blockers,
    handle_task_children,
    handle_task_detail,
    handle_task_events,
    handle_task_graph,
    handle_task_merge,
    handle_tasks_get,
    handle_trace_detail,
    handle_traces_get,
    handle_worker_detail,
    handle_worker_leases_get,
    handle_worker_nodes_get,
    handle_workers_get,
)


class _FakeRecord:
    def __init__(
        self,
        task_id: str,
        *,
        root_task_id: str = "",
        parent_task_id: str = "",
        owner: str = "orchestrator",
        status: str = "queued",
        principal: str = "tester",
        request_id: str = "req-1",
        run_id: str = "run-1",
    ) -> None:
        self.task_id = task_id
        self.root_task_id = root_task_id or task_id
        self.parent_task_id = parent_task_id
        self.owner = owner
        self.status = status
        self.principal = principal
        self.request_id = request_id
        self.run_id = run_id

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "root_task_id": self.root_task_id,
            "parent_task_id": self.parent_task_id,
            "owner": self.owner,
            "status": self.status,
        }


class _FakeEvent:
    def __init__(self, event_id: str) -> None:
        self.event_id = event_id

    def to_dict(self) -> dict:
        return {"event_id": self.event_id}


class _FakeArtifact:
    def __init__(self, artifact_id: str) -> None:
        self.artifact_id = artifact_id

    def to_dict(self) -> dict:
        return {"artifact_id": self.artifact_id}


class _FakeTraceRun:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id

    def to_dict(self) -> dict:
        return {"run_id": self.run_id}


class _FakeTraceStore:
    def list_runs(self, **kwargs):
        return [_FakeTraceRun("run-1")], 1

    def get_run(self, run_id: str):
        if run_id == "run-1":
            return {"run_id": run_id}
        return None


class _FakeStore:
    def __init__(self) -> None:
        self.root = _FakeRecord("task-root", status="running")
        self.child = _FakeRecord(
            "task-child",
            root_task_id="task-root",
            parent_task_id="task-root",
            owner="worker:planner",
            status="queued",
        )
        self.records = [self.root, self.child]

    def summary(self) -> dict:
        return {"total": len(self.records)}

    def get_task(self, task_id: str):
        return next((record for record in self.records if record.task_id == task_id), None)

    def get_events(self, task_id: str):
        return [_FakeEvent(f"evt-{task_id}")]

    def get_artifacts(self, task_id: str):
        return [_FakeArtifact(f"art-{task_id}")]

    def list_worker_nodes(self):
        return [{"node_id": "node-1", "worker_id": "planner", "queue_name": "planner.node-1"}]

    def list_execution_queues(self):
        return [{"queue_name": "planner.node-1", "worker_id": "planner"}]

    def worker_node_summary(self):
        return {"node_count": 1, "healthy_count": 1, "draining_count": 0, "queue_count": 1}

    def list_queue_leases(self, **kwargs):
        return [{"lease_id": "ql-1", "queue_name": "planner.node-1", "lease_status": "active"}]

    def queue_summary(self):
        return {"queue_count": 1, "lease_count": 1, "active_lease_count": 1}


class _FakeBot:
    def __init__(self) -> None:
        self.trace_store = _FakeTraceStore()

    def get_task_merge_payload(self, task_id: str, *, record=None) -> dict:
        return {"available": True, "review_status": "pending", "task_id": task_id}


class _FakeHandler:
    def __init__(self) -> None:
        self.botboy = _FakeBot()
        self.auth_enabled = False
        self.store = _FakeStore()
        self.json_payloads: list[tuple[dict, int]] = []

    def _json(self, payload: dict, status: int = 200) -> None:
        self.json_payloads.append((payload, status))

    def _ensure_access(self, require_auth: bool = False):
        return "tester"

    def _task_store(self, optional: bool = False):
        return self.store

    def _task_list_records(self, store, **kwargs):
        return store.records, len(store.records), store.records

    def _task_metrics(self, store) -> dict:
        return {
            "blockers": [{"task_id": "task-root"}],
            "blocked_count": 1,
            "delegated_count": 1,
            "running_count": 1,
            "queued_count": 1,
            "worker_count": 1,
            "handoff_queue_depth": 0,
            "oldest_blocked_age_s": 0,
            "oldest_running_age_s": 0,
            "by_worker": {"planner": 1},
            "by_blocked_kind": {"approval": 1},
        }

    def _task_workers_payload(self, store) -> dict:
        return {"available": True, "worker_count": 1, "registry": [{"worker_id": "planner"}]}

    def _task_records_by_root(self, store, root_task_id: str):
        return store.records

    def _direct_child_records(self, records, parent_task_id: str):
        return [record for record in records if record.parent_task_id == parent_task_id]

    def _decorate_task_record(self, store, record, root_records=None):
        payload = record.to_dict()
        payload["decorated"] = True
        return payload

    def _build_task_graph(self, store, records, task_id: str):
        return {"focus_task_id": task_id, "nodes": [record.task_id for record in records]}

    def _decorate_merge_payload(self, payload: dict) -> dict:
        return {**payload, "decorated": True}

    def _worker_lookup(self):
        return {"planner": {"worker_id": "planner", "display_name": "Planner"}}

    def _collect_task_records(self, store, root_task_id: str = ""):
        return store.records

    def _worker_id_from_owner(self, owner: str) -> str:
        return owner.split(":", 1)[1] if owner.startswith("worker:") else ""


class SimpleTaskReadHandlersTest(unittest.TestCase):
    def test_handle_traces(self) -> None:
        handler = _FakeHandler()
        handle_traces_get(handler, {"limit": ["5"], "offset": ["0"]})
        runs_payload, status = handler.json_payloads[-1]
        handle_trace_detail(handler, "run-1")
        trace_payload, trace_status = handler.json_payloads[-1]
        self.assertEqual(status, 200)
        self.assertEqual(trace_status, 200)
        self.assertEqual(runs_payload["total"], 1)
        self.assertEqual(trace_payload["run_id"], "run-1")

    def test_handle_task_views(self) -> None:
        handler = _FakeHandler()
        handle_tasks_get(handler, {"limit": ["5"], "offset": ["0"]})
        tasks_payload, _ = handler.json_payloads[-1]
        handle_task_detail(handler, "task-root")
        detail_payload, _ = handler.json_payloads[-1]
        handle_task_children(handler, "task-root")
        children_payload, _ = handler.json_payloads[-1]
        handle_task_graph(handler, "task-root")
        graph_payload, _ = handler.json_payloads[-1]
        handle_task_events(handler, "task-root")
        events_payload, _ = handler.json_payloads[-1]
        handle_task_artifacts(handler, "task-root")
        artifacts_payload, _ = handler.json_payloads[-1]
        self.assertTrue(tasks_payload["available"])
        self.assertEqual(detail_payload["task"]["task_id"], "task-root")
        self.assertEqual(children_payload["count"], 1)
        self.assertEqual(graph_payload["focus_task_id"], "task-root")
        self.assertEqual(events_payload["count"], 1)
        self.assertEqual(artifacts_payload["count"], 1)

    def test_handle_merge_blockers_and_workers(self) -> None:
        handler = _FakeHandler()
        handle_task_merge(handler, "task-root")
        merge_payload, _ = handler.json_payloads[-1]
        handle_task_blockers(handler, {"limit": ["10"]})
        blockers_payload, _ = handler.json_payloads[-1]
        handle_workers_get(handler, {})
        workers_payload, _ = handler.json_payloads[-1]
        handle_worker_nodes_get(handler, {})
        worker_nodes_payload, _ = handler.json_payloads[-1]
        handle_worker_leases_get(handler, {"queue_name": ["planner.node-1"]})
        worker_leases_payload, _ = handler.json_payloads[-1]
        handle_worker_detail(handler, "planner")
        worker_payload, _ = handler.json_payloads[-1]
        self.assertTrue(merge_payload["merge"]["decorated"])
        self.assertEqual(blockers_payload["total"], 1)
        self.assertEqual(workers_payload["worker_count"], 1)
        self.assertEqual(worker_nodes_payload["summary"]["node_count"], 1)
        self.assertEqual(worker_leases_payload["summary"]["active_lease_count"], 1)
        self.assertEqual(worker_leases_payload["leases"][0]["lease_id"], "ql-1")
        self.assertEqual(worker_payload["worker"]["worker_id"], "planner")
        self.assertEqual(worker_payload["task_count"], 1)


if __name__ == "__main__":
    unittest.main()
