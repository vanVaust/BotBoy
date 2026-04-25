from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from botboy.handoff_route_support import handle_handoff_command, resolve_active_parent_task_context


class _Store:
    def __init__(self, record=None) -> None:
        self.record = record

    def get_task(self, task_id: str):
        if self.record and task_id == self.record.task_id:
            return self.record
        return None


class _Bot:
    def __init__(self) -> None:
        self.task_store = _Store()
        self.called = {}

    def _handle_handoff_suggest(self, command: str) -> dict:
        self.called["suggest"] = command
        return {"success": True, "output": "suggest", "type": "handoff"}

    def _parse_handoff_batch_request(self, spec_text: str):
        self.called["batch_spec"] = spec_text
        return "last_child_wins", [("reviewer", "status")]

    def _task_context_from_record(self, record):
        return SimpleNamespace(task_id=record.task_id, request_id=record.request_id)

    async def _run_worker_handoff_batch(self, **kwargs):
        self.called["batch"] = kwargs
        return {"success": True, "output": "batch", "type": "handoff_batch"}

    async def _run_worker_handoff(self, **kwargs):
        self.called["single"] = kwargs
        return {"success": True, "output": "single", "type": "handoff"}


class HandoffRouteSupportTest(unittest.TestCase):
    def test_resolve_active_parent_task_context_returns_none_without_trace(self) -> None:
        bot = _Bot()
        with patch("botboy.handoff_route_support.get_trace_context", return_value=None):
            self.assertIsNone(resolve_active_parent_task_context(bot))

    def test_handoff_suggest_delegates(self) -> None:
        bot = _Bot()
        result = asyncio.run(handle_handoff_command(bot, "handoff suggest planner"))
        self.assertTrue(result["success"])
        self.assertEqual(bot.called["suggest"], "handoff suggest planner")

    def test_handoff_batch_requires_active_task_context(self) -> None:
        bot = _Bot()
        with patch("botboy.handoff_route_support.get_trace_context", return_value=None):
            result = asyncio.run(handle_handoff_command(bot, "handoff batch reviewer=status"))
        self.assertFalse(result["success"])
        self.assertEqual(result["type"], "handoff_batch")

    def test_single_handoff_delegates_with_active_task_context(self) -> None:
        bot = _Bot()
        bot.task_store = _Store(SimpleNamespace(task_id="task-1", request_id="req-1"))
        with patch(
            "botboy.handoff_route_support.get_trace_context",
            return_value=SimpleNamespace(task_id="task-1"),
        ):
            result = asyncio.run(
                handle_handoff_command(
                    bot,
                    "handoff reviewer status",
                    principal="release.test",
                    roles=["admin"],
                    approval_context={"granted": True},
                )
            )
        self.assertTrue(result["success"])
        self.assertEqual(bot.called["single"]["worker_id"], "reviewer")
        self.assertEqual(bot.called["single"]["delegated_command"], "status")
