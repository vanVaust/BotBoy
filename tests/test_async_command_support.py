from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from botboy.async_command_support import handle_evals, handle_task_resume_command


class _FakeRecord:
    def __init__(self, task_id: str, *, status: str = "blocked") -> None:
        self.task_id = task_id
        self.root_task_id = "root-1"
        self.parent_task_id = "parent-1"
        self.kind = "workflow"
        self.principal = "tester"
        self.request_id = "req-1"
        self.owner = "worker:reviewer"
        self.scheduler_task_id = ""
        self.command = "status"
        self.status = status
        self.run_id = "run-1"

    def to_context(self):
        return {"task_id": self.task_id}

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "parent_task_id": self.parent_task_id,
        }


class _FakeTaskStore:
    def __init__(self) -> None:
        self.record = _FakeRecord("task-1")
        self.events: list[dict] = []

    def get_task(self, task_id: str):
        if task_id == self.record.task_id:
            return self.record
        if task_id == self.record.parent_task_id:
            return _FakeRecord(self.record.parent_task_id, status="running")
        return None

    def add_event(self, task_id: str, **kwargs) -> None:
        self.events.append({"task_id": task_id, **kwargs})


class _FakeBot:
    def __init__(self) -> None:
        self.task_store = _FakeTaskStore()
        self.calls: list[dict] = []

    def _task_context_from_record(self, record):
        return record.to_context()

    def _sync_parent_after_child(self, record, *, principal: str, request_id: str) -> None:
        self.calls.append({"synced": record.task_id, "principal": principal, "request_id": request_id})

    async def process_command(self, command: str, **kwargs):
        self.calls.append({"command": command, **kwargs})
        self.task_store.record.status = "completed"
        return {"success": True, "output": "ok", "data": {"task": {"task_id": "task-1"}}}


class AsyncCommandSupportTest(unittest.TestCase):
    def test_handle_task_resume_command(self) -> None:
        bot = _FakeBot()
        result = asyncio.run(
            handle_task_resume_command(
                bot,
                "task resume task-1",
                principal="tester",
                roles=["operator"],
                request_id="req-main",
            )
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["resumed_from"]["task_id"], "task-1")
        self.assertEqual(bot.calls[0]["command"], "status")
        self.assertEqual(bot.task_store.events[0]["event_type"], "resume_requested")

    def test_handle_evals(self) -> None:
        bot = _FakeBot()
        bot.task_store = None
        result = asyncio.run(handle_evals(bot, "evals"))
        self.assertIn(result["type"], {"evals"})
        self.assertIn("success", result)

    def test_handle_evals_reports_runner_runtime_error(self) -> None:
        bot = _FakeBot()
        bot.task_store = None

        class _BrokenRunner:
            def run(self):
                raise RuntimeError("runner broke")

        with patch("botboy.evals.EvalReplayRunner.from_defaults", return_value=_BrokenRunner()):
            result = asyncio.run(handle_evals(bot, "evals"))
        self.assertFalse(result["success"])
        self.assertIn("runner broke", result["output"])


if __name__ == "__main__":
    unittest.main()
