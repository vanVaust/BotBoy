from __future__ import annotations

import unittest

from botboy.task_command_support import handle_task_command


class _FakeWorker:
    def to_dict(self) -> dict:
        return {"worker_id": "reviewer"}


class _FakeRecord:
    def __init__(self, task_id: str, *, status: str = "blocked") -> None:
        self.task_id = task_id
        self.root_task_id = "root"
        self.parent_task_id = "parent-1"
        self.status = status
        self.kind = "workflow"
        self.owner = "worker:reviewer"
        self.principal = "tester"
        self.request_id = "req-1"
        self.run_id = "run-1"
        self.scheduler_task_id = None
        self.delegation_status = "awaiting_merge"
        self.delegated_to_worker = "reviewer"
        self.blocked_by_task_id = None
        self.blocked_kind = None
        self.blocked_reason = None
        self.lease_expires_at = None
        self.heartbeat_at = None
        self.title = f"title-{task_id}"
        self.summary = "summary"
        self.command = "status"
        self.created_at = "2026-04-03T00:00:00+00:00"
        self.started_at = None
        self.ended_at = None

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class _FakeEvent:
    event_type = "updated"
    status = "ok"
    message = "done"
    request_id = "req-1"

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type,
            "status": self.status,
            "message": self.message,
            "request_id": self.request_id,
        }


class _FakeArtifact:
    category = "report"
    label = "merge-report"
    media_type = "text/plain"
    file_path = "artifact.txt"

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "label": self.label,
            "media_type": self.media_type,
            "file_path": self.file_path,
        }


class _FakeTaskStore:
    def __init__(self) -> None:
        self.record = _FakeRecord("task-1")

    def list_tasks(self, limit: int = 10):
        return [self.record], 1

    def list_blockers(self, limit: int = 20):
        return [self.record]

    def list_worker_leases(self, limit: int = 50):
        return [
            {
                "task_id": self.record.task_id,
                "worker_id": "reviewer",
                "status": "leased",
                "is_stale": False,
                "is_recoverable": True,
                "lease_age_s": 42,
                "parent_task_id": "parent-1",
            }
        ]

    def get_task(self, task_id: str):
        return self.record if task_id == self.record.task_id else None

    def list_children(self, task_id: str, limit: int = 50):
        return [self.record]

    def task_graph(self, task_id: str) -> dict:
        return {"tasks": [self.record.to_dict()]}

    def get_events(self, task_id: str):
        return [_FakeEvent()]

    def get_artifacts(self, task_id: str):
        return [_FakeArtifact()]

    def cancel_task(self, task_id: str, *, principal: str, request_id: str, reason: str):
        return self.record

    def recover_stale_worker_task(self, task_id: str, *, principal: str, request_id: str, run_id: str):
        return {"task_id": task_id, "status": "recovered"}

    def reassign_task(self, task_id: str, *, worker_id: str, principal: str, request_id: str, run_id: str):
        return self.record


class _FakeBot:
    def __init__(self) -> None:
        self.task_store = _FakeTaskStore()
        self.workers = {"reviewer": _FakeWorker()}

    def get_operator_workbench_payload(self, *, limit: int = 10) -> dict:
        return {
            "summary": {
                "merge_actionable_count": 1,
                "merge_total": 1,
                "blocked_count": 1,
                "recoverable_lease_count": 1,
                "stale_lease_count": 0,
            },
            "merge_queue": {
                "items": [
                    {
                        "task_id": "task-1",
                        "review_status": "needs_attention",
                        "next_action": "resolve_many",
                        "conflict_count": 1,
                        "override_count": 0,
                        "review_pending_keys": ["alpha"],
                    }
                ]
            },
            "blockers": [{"task_id": "task-1", "owner": "worker:reviewer", "blocked_kind": None, "blocked_by_task_id": None}],
            "recoverable_leases": [{"task_id": "task-1", "worker_id": "reviewer", "lease_age_s": 42, "parent_task_id": "parent-1"}],
            "stale_leases": [],
        }

    def get_merge_review_queue(self, *, limit: int = 10) -> dict:
        return {
            "actionable_count": 1,
            "total": 1,
            "items": [
                {
                    "task_id": "task-1",
                    "review_status": "needs_attention",
                    "next_action": "resolve_many",
                    "conflict_count": 1,
                    "override_count": 0,
                    "review_pending_keys": ["alpha"],
                }
            ],
        }

    def set_task_merge_resolution_policy(self, task_id: str, *, policy: str, principal: str, request_id: str):
        return self.task_store.record

    def apply_task_merge_review_action(self, task_id: str, *, action: str, **kwargs):
        return self.task_store.record

    def get_task_merge_payload(self, task_id: str, *, record=None) -> dict:
        return {
            "available": True,
            "merge_policy": "child_chain",
            "configured_resolution_policy": "prefer_non_null",
            "configured_resolution_overrides": {"alpha": "reviewer"},
            "resolution_policy": "prefer_non_null",
            "effective_resolution_policy": "prefer_non_null",
            "available_resolution_policies": ["prefer_non_null"],
            "review_status": "needs_attention",
            "review_state": "needs_attention",
            "next_action": "resolve_many",
            "actionable": True,
            "policy_delta": "aligned",
            "available_review_actions": ["resolve_many"],
            "available_review_presets": [{"id": "clear_overrides"}],
            "completed_child_count": 1,
            "active_child_count": 0,
            "linked_artifact_count": 1,
            "override_count": 1,
            "workers_involved": ["reviewer"],
            "valid_sources": ["reviewer"],
            "known_review_keys": ["alpha"],
            "review_pending_keys": ["alpha"],
            "applied_override_keys": ["alpha"],
            "pending_child_ids": [],
            "merge_resolution": {
                "conflict_count": 1,
                "resolved_keys": ["alpha"],
                "conflicts": [],
            },
        }

    def _merge_resolution_policy_for_record(self, record) -> str:
        return "prefer_non_null"


class TaskCommandSupportTest(unittest.TestCase):
    def test_handle_task_command_list(self) -> None:
        result = handle_task_command(_FakeBot(), "task list")
        self.assertTrue(result["success"])
        self.assertIn("Last 1 of 1 tasks", result["output"])

    def test_handle_task_command_merge_resolve_many(self) -> None:
        result = handle_task_command(
            _FakeBot(),
            "task merge resolve-many task-1 alpha=reviewer beta=reviewer",
            principal="tester",
            request_id="req-2",
        )
        self.assertTrue(result["success"])
        self.assertIn("2 key(s)", result["output"])

    def test_handle_task_command_show_merge(self) -> None:
        result = handle_task_command(_FakeBot(), "task merge task-1")
        self.assertTrue(result["success"])
        self.assertIn("Merge review for task-1", result["output"])

    def test_handle_task_command_resume_delegates_to_async_path(self) -> None:
        result = handle_task_command(_FakeBot(), "task resume task-1")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
