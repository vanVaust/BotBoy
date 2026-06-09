from __future__ import annotations

import json
import shutil
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from botboy.task_merge_service import TaskMergeService
from botboy.tasks import (
    TASK_STATUS_BLOCKED,
    TASK_STATUS_COMPLETED,
    TASK_STATUS_QUEUED,
    TASK_STATUS_RUNNING,
    TASK_STATUS_WAITING_APPROVAL,
)


@dataclass
class FakeTaskRecord:
    task_id: str
    payload: dict = field(default_factory=dict)
    result: dict = field(default_factory=dict)
    status: str = TASK_STATUS_COMPLETED
    owner: str = "owner"
    title: str = "Task"
    root_task_id: str = "root"
    updated_at: str = "2026-04-03T12:00:00"
    run_id: str = "run-1"


class FakeTaskStore:
    def __init__(self, *, records=None, children=None, artifact_root=None):
        self.records = {record.task_id: record for record in (records or [])}
        self.children = children or {}
        self.artifact_root = Path(
            artifact_root or (Path.cwd() / ".botboy-runtime" / "task-merge-service-tests")
        )
        self.artifacts = {}
        self.events = []

    def get_task(self, task_id):
        return self.records.get(task_id)

    def update_task(self, task_id, **updates):
        record = self.records[task_id]
        for key, value in updates.items():
            setattr(record, key, value)
        return record

    def list_children(self, task_id, limit=200):
        return list(self.children.get(task_id, []))[:limit]

    def get_artifacts(self, task_id, limit=20):
        return list(self.artifacts.get(task_id, []))[:limit]

    def write_artifact(self, task_id, *, category, label, filename, content, media_type):
        task_dir = self.artifact_root / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        file_path = task_dir / filename
        file_path.write_text(content, encoding="utf-8")
        artifact = SimpleNamespace(
            category=category,
            label=label,
            file_path=str(file_path),
            media_type=media_type,
        )
        self.artifacts.setdefault(task_id, []).append(artifact)
        return artifact

    def add_event(self, task_id, **event):
        self.events.append({"task_id": task_id, **event})
        return self.events[-1]

    def list_tasks(self, limit=100):
        records = list(self.records.values())
        return records[:limit], len(records)

    def summary(self):
        blocked_records = [record for record in self.records.values() if record.status in {TASK_STATUS_WAITING_APPROVAL, TASK_STATUS_BLOCKED}]
        return {
            "blocked": [
                {
                    "task_id": record.task_id,
                    "status": record.status,
                    "title": record.title,
                }
                for record in blocked_records
            ],
            "blocked_count": len(blocked_records),
            "recoverable_leases": [],
            "recoverable_lease_count": 0,
            "stale_leases": [],
            "stale_lease_count": 0,
        }


class RecordingService(TaskMergeService):
    def __init__(self):
        super().__init__(SimpleNamespace(task_store=None))
        self.calls = []

    def set_task_merge_resolution_override(self, task_id: str, **kwargs):
        self.calls.append(("override", task_id, kwargs))
        return "override"

    def set_task_merge_resolution_overrides_bulk(self, task_id: str, **kwargs):
        self.calls.append(("bulk_override", task_id, kwargs))
        return "bulk_override"

    def resolve_all_task_merge_keys_by_source(self, task_id: str, **kwargs):
        self.calls.append(("resolve_all", task_id, kwargs))
        return "resolve_all"

    def clear_task_merge_resolution_override(self, task_id: str, **kwargs):
        self.calls.append(("clear", task_id, kwargs))
        return "clear"

    def clear_task_merge_resolution_overrides_bulk(self, task_id: str, **kwargs):
        self.calls.append(("bulk_clear", task_id, kwargs))
        return "bulk_clear"

    def reapply_task_merge_resolution(self, task_id: str, **kwargs):
        self.calls.append(("reapply", task_id, kwargs))
        return "reapply"

    def apply_task_merge_review_preset(self, task_id: str, **kwargs):
        self.calls.append(("preset", task_id, kwargs))
        return "preset"


def build_child_merge(child_id: str, worker: str, data: dict, *, status: str = TASK_STATUS_COMPLETED, summary: str = "") -> dict:
    return {
        "merged_from_child_task_id": child_id,
        "merged_from_worker": worker,
        "merged_from_owner": f"worker:{worker}",
        "child_status": status,
        "child_summary": summary or f"merge from {worker}",
        "child_result": {"data": data},
        "linked_artifacts": [{"name": f"{child_id}.json"}],
    }


class TaskMergeServiceTests(unittest.TestCase):
    def test_load_task_merge_result_prefers_direct_nested_and_best_artifact(self):
        direct_record = FakeTaskRecord(
            task_id="direct",
            result={"merge_policy": "direct", "child_merges": []},
        )
        nested_record = FakeTaskRecord(
            task_id="nested",
            result={"data": {"merge": {"merge_policy": "nested", "child_merges": []}}},
        )

        artifact_root = Path.cwd() / ".botboy-runtime" / "task-merge-service-tests" / "load"
        shutil.rmtree(artifact_root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, artifact_root.parent, ignore_errors=True)
        artifact_record = FakeTaskRecord(task_id="artifact")
        store = FakeTaskStore(records=[direct_record, nested_record, artifact_record], artifact_root=artifact_root)
        service = TaskMergeService(SimpleNamespace(task_store=store))

        best_path = artifact_root / "artifact" / "best.json"
        worse_path = artifact_root / "artifact" / "worse.json"
        best_path.parent.mkdir(parents=True, exist_ok=True)
        worse_path.parent.mkdir(parents=True, exist_ok=True)
        best_path.write_text(
            json.dumps(
                {
                    "merge_policy": "artifact-best",
                    "completed_child_count": 2,
                    "active_child_count": 0,
                    "linked_artifact_count": 2,
                    "merge_resolution": {"conflict_count": 0},
                }
            ),
            encoding="utf-8",
        )
        worse_path.write_text(
            json.dumps(
                {
                    "merge_policy": "artifact-worse",
                    "completed_child_count": 1,
                    "active_child_count": 2,
                    "linked_artifact_count": 0,
                    "merge_resolution": {"conflict_count": 3},
                }
            ),
            encoding="utf-8",
        )
        store.artifacts["artifact"] = [
            SimpleNamespace(category="merge_report", file_path=str(worse_path)),
            SimpleNamespace(category="merge_report", file_path=str(best_path)),
        ]

        self.assertEqual(service.load_task_merge_result(direct_record)["merge_policy"], "direct")
        self.assertEqual(service.load_task_merge_result(nested_record)["merge_policy"], "nested")
        self.assertEqual(service.load_task_merge_result(artifact_record)["merge_policy"], "artifact-best")

    def test_build_parent_merge_result_refreshes_payload_and_writes_artifact(self):
        child_merges = [
            build_child_merge("child-1", "reviewer", {"title": "", "description": "first"}),
            build_child_merge("child-2", "planner", {"title": "final title", "description": "first"}),
        ]
        parent_record = FakeTaskRecord(
            task_id="parent",
            payload={
                "merge_resolution_policy": "prefer_non_null",
                "merge_resolution_overrides": {"title": "worker:planner"},
            },
            result={"merge_policy": "multi_child_sequential_accumulator", "child_merges": child_merges},
            status=TASK_STATUS_RUNNING,
        )
        sibling_running = FakeTaskRecord(task_id="sibling-1", status=TASK_STATUS_RUNNING)
        sibling_completed = FakeTaskRecord(task_id="sibling-2", status=TASK_STATUS_COMPLETED)
        store = FakeTaskStore(
            records=[parent_record, sibling_running, sibling_completed],
            children={"parent": [sibling_running, sibling_completed]},
        )
        service = TaskMergeService(SimpleNamespace(task_store=store))

        merge_result = service.build_parent_merge_result(parent_record, child_merges=child_merges)
        self.assertTrue(merge_result["success"])
        self.assertEqual(merge_result["configured_resolution_policy"], "prefer_non_null")
        self.assertEqual(merge_result["resolution_policy"], "prefer_non_null")
        self.assertEqual(merge_result["review_status"], "overridden")
        self.assertEqual(merge_result["next_action"], "wait_children")
        self.assertEqual(merge_result["active_child_count"], 1)
        self.assertEqual(merge_result["completed_child_count"], 1)
        self.assertEqual(merge_result["linked_artifact_count"], 1)
        self.assertIn("title", merge_result["applied_override_keys"])
        self.assertIn("title", merge_result["known_review_keys"])
        self.assertIn("worker:planner", merge_result["valid_sources"])
        self.assertGreaterEqual(len(merge_result["available_review_presets"]), 2)

        with patch("botboy.task_merge_service.time.time", return_value=1234.5):
            refreshed = service.refresh_task_merge_review(
                "parent",
                principal="tester",
                request_id="req-1",
                event_type="merge_review_refreshed",
                message="Merge review refreshed",
            )
        self.assertEqual(refreshed.task_id, "parent")
        self.assertEqual(len(store.events), 1)
        self.assertEqual(store.events[0]["event_type"], "merge_review_refreshed")
        self.assertEqual(len(store.get_artifacts("parent")), 1)
        artifact_content = Path(store.get_artifacts("parent")[0].file_path).read_text(encoding="utf-8")
        self.assertIn('"merge_policy": "multi_child_sequential_accumulator"', artifact_content)

    def test_set_policy_and_get_task_merge_payload(self):
        child_merges = [
            build_child_merge("child-1", "reviewer", {"title": "first"}),
            build_child_merge("child-2", "planner", {"title": "second", "details": {"a": 1}}),
        ]
        parent_record = FakeTaskRecord(
            task_id="parent",
            payload={},
            result={"merge_policy": "multi_child_sequential_accumulator", "child_merges": child_merges},
        )
        store = FakeTaskStore(records=[parent_record])
        service = TaskMergeService(SimpleNamespace(task_store=store))

        built_merge = service.build_parent_merge_result(parent_record, child_merges=child_merges)
        parent_record.result = built_merge

        updated = service.set_task_merge_resolution_policy(
            "parent",
            policy="prefer_richer_value",
            principal="tester",
            request_id="req-2",
        )
        self.assertEqual(updated.payload["merge_resolution_policy"], "prefer_richer_value")
        self.assertEqual(len(store.events), 1)
        self.assertEqual(store.events[0]["event_type"], "merge_policy_updated")

        payload = service.get_task_merge_payload("parent", record=updated)
        self.assertTrue(payload["available"])
        self.assertEqual(payload["configured_resolution_policy"], "prefer_richer_value")
        self.assertIn("title", payload["known_review_keys"])
        self.assertIn("worker:planner", payload["valid_sources"])
        self.assertIn("prefer_reviewer", {item["preset"] for item in payload["available_review_presets"] if "preset" in item})

    def test_apply_task_merge_review_action_dispatches(self):
        service = RecordingService()
        cases = [
            ("resolve_key", "override", {"key": "title", "source": "worker:planner"}),
            ("resolve_many", "bulk_override", {"items": [{"key": "title", "source": "worker:planner"}]}),
            ("resolve_all", "resolve_all", {"source": "worker:planner", "keys": ["title"]}),
            ("clear", "clear", {"key": "title"}),
            ("clear_many", "bulk_clear", {"keys": ["title"]}),
            ("reapply", "reapply", {}),
            ("apply_preset", "preset", {"preset": "prefer_reviewer"}),
        ]
        for action, expected_call, kwargs in cases:
            with self.subTest(action=action):
                service.calls.clear()
                result = service.apply_task_merge_review_action(
                    "parent",
                    action=action,
                    principal="tester",
                    request_id="req-3",
                    **kwargs,
                )
                self.assertEqual(result, expected_call)
                self.assertEqual(service.calls[0][0], expected_call)

    def test_merge_queue_and_workbench_payloads_use_shared_signals(self):
        child_merges = [
            build_child_merge("child-1", "reviewer", {"title": ""}),
            build_child_merge("child-2", "planner", {"title": "final"}),
        ]
        parent_record = FakeTaskRecord(
            task_id="parent",
            payload={"merge_resolution_overrides": {"title": "worker:planner"}},
            result={"merge_policy": "multi_child_sequential_accumulator", "child_merges": child_merges},
            status=TASK_STATUS_RUNNING,
            title="Parent",
        )
        sibling_running = FakeTaskRecord(task_id="sibling-running", status=TASK_STATUS_RUNNING, title="Sibling running")
        blocked_record = FakeTaskRecord(task_id="blocked", status=TASK_STATUS_BLOCKED, title="Blocked task")
        store = FakeTaskStore(
            records=[parent_record, blocked_record, sibling_running],
            children={"parent": [sibling_running]},
        )
        service = TaskMergeService(SimpleNamespace(task_store=store))

        parent_record.result = service.build_parent_merge_result(parent_record, child_merges=child_merges)

        queue = service.get_merge_review_queue(limit=10)
        self.assertTrue(queue["available"])
        self.assertEqual(queue["total"], 1)
        self.assertEqual(queue["actionable_count"], 1)
        self.assertEqual(queue["items"][0]["task_id"], "parent")

        workbench = service.get_operator_workbench_payload(limit=10)
        self.assertTrue(workbench["available"])
        self.assertEqual(workbench["merge_queue"]["total"], 1)
        self.assertEqual(workbench["summary"]["merge_actionable_count"], 1)
        self.assertEqual(workbench["summary"]["blocked_count"], 1)
        self.assertEqual(workbench["blockers"][0]["task_id"], "blocked")
