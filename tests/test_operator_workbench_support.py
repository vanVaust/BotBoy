from __future__ import annotations

import unittest

from botboy.operator_workbench_support import (
    get_merge_review_queue,
    get_operator_workbench_payload,
    merge_review_signal_fields,
    summarize_merge_queue_item,
)


class _FakeRecord:
    def __init__(self, task_id: str, *, updated_at: str, conflict_count: int = 0, actionable: bool = True) -> None:
        self.task_id = task_id
        self.root_task_id = "root"
        self.status = "blocked"
        self.owner = "worker:reviewer"
        self.title = f"title-{task_id}"
        self.updated_at = updated_at
        self.conflict_count = conflict_count
        self.actionable = actionable


class _FakeTaskStore:
    def __init__(self, records: list[_FakeRecord]) -> None:
        self.records = records

    def list_tasks(self, limit: int = 100):
        return self.records[:limit], len(self.records)

    def summary(self):
        return {
            "blocked_count": 2,
            "recoverable_lease_count": 1,
            "stale_lease_count": 1,
            "recoverable_leases": [{"task_id": "lease-1"}],
            "stale_leases": [{"task_id": "lease-2"}],
            "blocked": [{"task_id": "blocked-1"}],
        }


class _FakeBot:
    def __init__(self, records: list[_FakeRecord]) -> None:
        self.task_store = _FakeTaskStore(records)

    def _merge_review_next_action(self, *, pending_child_ids, review_pending_keys, override_count):
        if pending_child_ids:
            return "wait_children"
        if review_pending_keys:
            return "resolve_many"
        if override_count:
            return "review_overrides"
        return "none"

    def _merge_available_review_presets(self, merge):
        return ["clear_overrides", "resolve_all"]

    def _merge_known_review_keys(self, merge):
        return list(merge.get("review_pending_keys", []))

    def _merge_valid_sources(self, merge):
        return list(merge.get("workers_involved", []))

    def get_task_merge_payload(self, task_id: str, *, record=None):
        conflict_count = getattr(record, "conflict_count", 0)
        actionable = getattr(record, "actionable", True)
        return {
            "available": True,
            "merge_policy": "test",
            "configured_resolution_policy": "prefer_non_null",
            "resolution_policy": "prefer_non_null",
            "review_status": "needs_attention" if actionable else "clean",
            "review_pending_keys": ["alpha"] if actionable else [],
            "applied_override_keys": [],
            "pending_child_ids": [],
            "workers_involved": ["reviewer"],
            "override_count": 0,
            "merge_resolution": {"conflict_count": conflict_count, "policy": "prefer_non_null"},
        }


class OperatorWorkbenchSupportTest(unittest.TestCase):
    def test_merge_review_signal_fields(self) -> None:
        bot = _FakeBot([])
        fields = merge_review_signal_fields(
            bot,
            {
                "configured_resolution_policy": "prefer_non_null",
                "resolution_policy": "last_child_wins",
                "review_pending_keys": ["alpha"],
                "pending_child_ids": ["child-1"],
                "override_count": 1,
            },
        )
        self.assertTrue(fields["actionable"])
        self.assertTrue(fields["override_active"])
        self.assertEqual(fields["next_action"], "wait_children")
        self.assertEqual(fields["policy_delta"], "prefer_non_null -> last_child_wins")

    def test_get_merge_review_queue_sorts_actionable_items(self) -> None:
        records = [
            _FakeRecord("task-1", updated_at="2026-04-03T12:00:00+00:00", conflict_count=1, actionable=True),
            _FakeRecord("task-2", updated_at="2026-04-03T11:00:00+00:00", conflict_count=3, actionable=True),
            _FakeRecord("task-3", updated_at="2026-04-03T10:00:00+00:00", conflict_count=0, actionable=False),
        ]
        bot = _FakeBot(records)
        queue = get_merge_review_queue(bot, limit=10)
        self.assertEqual([item["task_id"] for item in queue["items"]], ["task-2", "task-1"])
        self.assertEqual(queue["actionable_count"], 2)

    def test_get_operator_workbench_payload_includes_summary(self) -> None:
        records = [_FakeRecord("task-1", updated_at="2026-04-03T12:00:00+00:00")]
        bot = _FakeBot(records)
        payload = get_operator_workbench_payload(bot, limit=5)
        self.assertTrue(payload["available"])
        self.assertEqual(payload["summary"]["merge_actionable_count"], 1)
        self.assertEqual(payload["summary"]["blocked_count"], 2)
        self.assertEqual(payload["recoverable_leases"][0]["task_id"], "lease-1")

    def test_summarize_merge_queue_item(self) -> None:
        record = _FakeRecord("task-1", updated_at="2026-04-03T12:00:00+00:00", conflict_count=2)
        bot = _FakeBot([record])
        summary = summarize_merge_queue_item(bot, record, bot.get_task_merge_payload(record.task_id, record=record))
        self.assertEqual(summary["task_id"], "task-1")
        self.assertEqual(summary["conflict_count"], 2)
        self.assertTrue(summary["actionable"])


if __name__ == "__main__":
    unittest.main()
