from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from botboy.command_execution_service import CommandExecutionService
from botboy.tasks import TASK_STATUS_COMPLETED, TASK_STATUS_FAILED


class _TaskStore:
    def __init__(self) -> None:
        self.updates = []

    def mark_running(self, *_args, **_kwargs):
        return None

    def attach_run(self, *_args, **_kwargs):
        return None

    def update_status(self, task_id, **kwargs):
        self.updates.append((task_id, kwargs))


class _Skills:
    def get_contract_for_command(self, _command):
        return {"needs_approval": True}


class _Bot:
    def __init__(self):
        self.cache = None
        self.skills = _Skills()
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
        return SimpleNamespace(
            task_id="task-approval",
            root_task_id="task-approval",
            parent_task_id="",
            command=command,
            principal=kwargs.get("principal", "alice"),
            request_id=kwargs.get("request_id", ""),
            org_id="org-a",
        )

    def _start_trace_run(self, *_args):
        return SimpleNamespace(run_id="run-approval", trace_id="trace-approval"), "span", "token"

    async def _route(self, command, **kwargs):
        self.route_calls.append((command, kwargs))
        return {"success": True, "output": "executed", "type": "status"}

    def _decorate_with_task(self, result, task_ctx, status):
        result.setdefault("data", {})["task"] = {"task_id": task_ctx.task_id, "status": status}
        return result

    def _finish_trace_run(self, *args, **kwargs):
        self.finished.append((args, kwargs))

    def _infer_task_status(self, result):
        return TASK_STATUS_COMPLETED if result.get("success") else TASK_STATUS_FAILED


class CommandApprovalBoundaryTests(unittest.TestCase):
    def test_approval_gated_command_cannot_use_local_default(self):
        bot = _Bot()
        result = asyncio.run(
            CommandExecutionService(bot).process_command(
                "dangerous operation",
                principal="alice",
            )
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["type"], "authorization_error")
        self.assertEqual(bot.route_calls, [])

    def test_approval_gated_command_accepts_only_server_issued_context(self):
        bot = _Bot()
        result = asyncio.run(
            CommandExecutionService(bot).process_command(
                "dangerous operation",
                principal="alice",
                approval_context={
                    "granted": True,
                    "explicit": True,
                    "source": "approval_store",
                    "approval_id": "approval-1",
                    "approval_scope": "task.execute",
                },
            )
        )
        self.assertTrue(result["success"])
        self.assertEqual(len(bot.route_calls), 1)

    def test_boolean_approval_flag_is_not_an_authorization_grant(self):
        bot = _Bot()
        result = asyncio.run(
            CommandExecutionService(bot).process_command(
                "dangerous operation",
                principal="alice",
                approval_context={"granted": True, "explicit": True, "approval": True},
            )
        )
        self.assertFalse(result["success"])
        self.assertEqual(bot.route_calls, [])


if __name__ == "__main__":
    unittest.main()
