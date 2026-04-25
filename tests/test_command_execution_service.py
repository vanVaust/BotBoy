from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from botboy.command_execution_service import CommandExecutionService
from botboy.tasks import TASK_STATUS_COMPLETED, TASK_STATUS_FAILED


class _TaskStore:
    def __init__(self) -> None:
        self.running = []
        self.status_updates = []

    def attach_run(self, task_id: str, run_id: str) -> None:
        self.running.append(("attach", task_id, run_id))

    def mark_running(self, task_id: str, **kwargs) -> None:
        self.running.append(("running", task_id, kwargs))

    def update_status(self, task_id: str, **kwargs) -> None:
        self.status_updates.append((task_id, kwargs))

    def write_artifact(self, *args, **kwargs) -> None:
        return None


class _Validator:
    def __init__(self, *, valid: bool, sanitized: str = "", errors: list[str] | None = None) -> None:
        self.valid = valid
        self.sanitized = sanitized
        self.errors = errors or []

    def validate_command(self, _command: str):
        return SimpleNamespace(valid=self.valid, sanitized=self.sanitized, errors=self.errors)


class _Bot:
    def __init__(self) -> None:
        self.cache = None
        self.skills = None
        self.validator = None
        self.task_store = _TaskStore()
        self.metrics = None
        self.trace_store = None
        self.archetypes = None
        self.reflection = None
        self.llm = None
        self.monitor = None
        self.history = None
        self.route_calls = []
        self.finished = []

    def _create_task_context(self, command: str, **kwargs):
        return SimpleNamespace(task_id="task-1", root_task_id="task-1", parent_task_id="", command=command, **kwargs)

    def _start_trace_run(self, *_args):
        return SimpleNamespace(run_id="run-1", trace_id="trace-1"), "span-1", "token-1"

    async def _route(self, command: str, **kwargs):
        self.route_calls.append((command, kwargs))
        return {"success": True, "output": f"ok:{command}", "type": "status"}

    def _decorate_with_task(self, result: dict, task_ctx, status: str) -> dict:
        result.setdefault("data", {})
        result["data"]["task"] = {"task_id": task_ctx.task_id, "status": status}
        return result

    def _finish_trace_run(self, trace_ctx, root_span_id, trace_token, **kwargs) -> None:
        self.finished.append((trace_ctx.run_id, root_span_id, trace_token, kwargs))

    def _infer_task_status(self, result: dict) -> str:
        return TASK_STATUS_COMPLETED if result.get("success") else TASK_STATUS_FAILED


class CommandExecutionServiceTest(unittest.TestCase):
    def test_empty_command_returns_error(self) -> None:
        result = asyncio.run(CommandExecutionService(_Bot()).process_command("   "))
        self.assertFalse(result["success"])
        self.assertEqual(result["type"], "error")

    def test_validation_failure_returns_failed_task_result(self) -> None:
        bot = _Bot()
        bot.validator = _Validator(valid=False, errors=["bad input"])
        result = asyncio.run(CommandExecutionService(bot).process_command("status"))
        self.assertFalse(result["success"])
        self.assertIn("Invalid input: bad input", result["output"])
        self.assertEqual(result["data"]["task"]["status"], TASK_STATUS_FAILED)
        self.assertEqual(bot.route_calls, [])

    def test_successful_command_routes_and_finishes_trace(self) -> None:
        bot = _Bot()
        bot.validator = _Validator(valid=True, sanitized="status")
        result = asyncio.run(CommandExecutionService(bot).process_command("status", principal="release.test"))
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["task"]["status"], TASK_STATUS_COMPLETED)
        self.assertEqual(bot.route_calls[0][0], "status")
        self.assertEqual(bot.finished[-1][3]["cmd_type"], "status")
