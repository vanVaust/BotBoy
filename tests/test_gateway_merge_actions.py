from __future__ import annotations

import unittest

from botboy.gateway.merge_actions import GatewayMergeActionError, build_merge_action_payload


class _FakeTask:
    def __init__(self, task_id: str, *, root_task_id: str = "root", principal: str = "user", request_id: str = "req-1") -> None:
        self.task_id = task_id
        self.root_task_id = root_task_id
        self.principal = principal
        self.request_id = request_id


class _FakeStore:
    def __init__(self, task: _FakeTask | None) -> None:
        self.task = task

    def get_task(self, task_id: str) -> _FakeTask | None:
        if self.task and self.task.task_id == task_id:
            return self.task
        return None


class _FakeBot:
    def __init__(self, task: _FakeTask) -> None:
        self.task = task
        self.calls: list[dict[str, object]] = []
        self.merge_payload = {"available": True, "configured_resolution_overrides": {}}

    def apply_task_merge_review_action(self, task_id: str, **kwargs):
        self.calls.append({"task_id": task_id, **kwargs})
        return self.task

    def get_task_merge_payload(self, task_id: str, *, record=None):
        return self.merge_payload


class GatewayMergeActionsTest(unittest.TestCase):
    def test_resolve_many_builds_bulk_response(self) -> None:
        task = _FakeTask("task-1")
        store = _FakeStore(task)
        bot = _FakeBot(task)

        result = build_merge_action_payload(
            bot,
            store=store,
            task_id="task-1",
            action="resolve_many",
            source="worker:planner",
            items=[{"key": "alpha"}],
            keys=["beta"],
            key="gamma",
            decorate_merge_payload=lambda payload: payload,
            decorate_task_record=lambda _store, record, root_records=None: {
                "task_id": record.task_id,
                "root_count": len(root_records or []),
            },
            task_records_by_root=lambda _store, _root_task_id: [task],
        )

        self.assertEqual(result["applied_count"], 3)
        self.assertEqual([item["key"] for item in result["bulk_results"]], ["alpha", "beta", "gamma"])
        self.assertEqual(bot.calls[0]["action"], "resolve_many")
        self.assertEqual(
            bot.calls[0]["items"],
            [
                {"key": "alpha", "source": "worker:planner"},
                {"key": "beta", "source": "worker:planner"},
                {"key": "gamma", "source": "worker:planner"},
            ],
        )

    def test_apply_preset_clear_overrides_without_overrides_returns_empty_result(self) -> None:
        task = _FakeTask("task-1")
        store = _FakeStore(task)
        bot = _FakeBot(task)

        result = build_merge_action_payload(
            bot,
            store=store,
            task_id="task-1",
            action="apply_preset",
            preset="clear_overrides",
            decorate_merge_payload=lambda payload: payload,
            decorate_task_record=lambda _store, record, root_records=None: {"task_id": record.task_id},
            task_records_by_root=lambda _store, _root_task_id: [task],
        )

        self.assertEqual(result["preset"], "clear_overrides")
        self.assertEqual(result["applied_count"], 0)
        self.assertEqual(result["bulk_results"], [])

    def test_missing_task_raises_lookup_error(self) -> None:
        with self.assertRaises(GatewayMergeActionError) as ctx:
            build_merge_action_payload(
                _FakeBot(_FakeTask("task-1")),
                store=_FakeStore(None),
                task_id="missing",
                action="refresh",
                decorate_merge_payload=lambda payload: payload,
                decorate_task_record=lambda _store, record, root_records=None: {},
                task_records_by_root=lambda _store, _root_task_id: [],
            )
        self.assertEqual(ctx.exception.status_code, 404)

    def test_missing_source_for_resolve_all_by_source_raises_value_error(self) -> None:
        task = _FakeTask("task-1")
        with self.assertRaises(GatewayMergeActionError) as ctx:
            build_merge_action_payload(
                _FakeBot(task),
                store=_FakeStore(task),
                task_id="task-1",
                action="resolve_all_by_source",
                decorate_merge_payload=lambda payload: payload,
                decorate_task_record=lambda _store, record, root_records=None: {},
                task_records_by_root=lambda _store, _root_task_id: [task],
            )
        self.assertEqual(ctx.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
