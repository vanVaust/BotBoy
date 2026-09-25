from __future__ import annotations

import asyncio
import tempfile
import unittest

from fastapi import HTTPException

import botboy.gateway  # noqa: F401,E402
from botboy.approval_store import ApprovalStore
from botboy.command_execution_service import CommandExecutionService
from botboy.gateway.execution_security_gate import _require_final_authorization
from botboy.security_context import SecurityContext
from botboy.task_security import TaskSecurityStore
from botboy.tasks import TaskStore


class _Skills:
    def get_contract_for_command(self, _command):
        return {"needs_approval": True}


class _Bot:
    def __init__(self, store):
        self.task_store = store
        self.skills = _Skills()
        self.route_calls = 0

    async def _route(self, *_args, **_kwargs):
        self.route_calls += 1
        return {"success": True, "type": "status", "output": "executed"}


class FinalExecutionAuthorizationTests(unittest.TestCase):
    def _setup(self):
        tmp = tempfile.TemporaryDirectory()
        store = TaskStore(db_path=f"{tmp.name}/botboy.db")
        task = store.create_task(
            title="approval boundary",
            principal="alice",
            org_id="tenant-a",
            command="dangerous operation",
        )
        TaskSecurityStore(store).save(
            SecurityContext.from_legacy(
                principal="alice",
                org_id="tenant-a",
                task_id=task.task_id,
                request_id="req-1",
            )
        )
        return tmp, store, task

    def _route_kwargs(self, task, approval_context):
        return {
            "principal_id": "alice",
            "roles": [],
            "approval_context": approval_context,
            "request_id": "req-1",
            "task_ctx": task,
            "trace_ctx": None,
            "root_span_id": "",
            "trace_token": None,
        }

    def _assert_gate_denies(self, service, command, task, approval_context):
        with self.assertRaises(HTTPException) as raised:
            _require_final_authorization(
                service,
                command,
                principal_id="alice",
                approval_context=approval_context,
                task_ctx=task,
            )
        self.assertEqual(raised.exception.status_code, 403)

    def test_final_gate_blocks_missing_approval_even_if_called_directly(self):
        tmp, store, task = self._setup()
        try:
            bot = _Bot(store)
            service = CommandExecutionService(bot)
            self.assertTrue(service._approval_required("dangerous operation"))
            self._assert_gate_denies(service, "dangerous operation", task, {})
            self.assertEqual(bot.route_calls, 0)
        finally:
            store.close()
            tmp.cleanup()

    def test_final_gate_requires_a_consumed_server_approval(self):
        tmp, store, task = self._setup()
        try:
            approvals = ApprovalStore(store)
            issued = approvals.issue(
                task_id=task.task_id,
                principal_id="alice",
                org_id="tenant-a",
                command=task.command,
            )
            approval_context = {
                "granted": True,
                "explicit": True,
                "source": "approval_store",
                "approval_id": issued["approval_id"],
                "approval_scope": "task.execute",
            }
            bot = _Bot(store)
            service = CommandExecutionService(bot)
            self.assertTrue(service._approval_required("dangerous operation"))
            self._assert_gate_denies(service, "dangerous operation", task, approval_context)
            self.assertEqual(bot.route_calls, 0)

            consumed = approvals.consume_if_valid(
                issued["approval_id"],
                task_id=task.task_id,
                principal_id="alice",
                org_id="tenant-a",
                command=task.command,
            )
            self.assertIsNotNone(consumed)
            result = asyncio.run(service._run_route("dangerous operation", **self._route_kwargs(task, approval_context)))
            self.assertTrue(result["success"])
            self.assertEqual(bot.route_calls, 1)
        finally:
            store.close()
            tmp.cleanup()

    def test_final_gate_rejects_command_fingerprint_mismatch(self):
        tmp, store, task = self._setup()
        try:
            approvals = ApprovalStore(store)
            issued = approvals.issue(
                task_id=task.task_id,
                principal_id="alice",
                org_id="tenant-a",
                command=task.command,
            )
            approvals.consume_if_valid(
                issued["approval_id"],
                task_id=task.task_id,
                principal_id="alice",
                org_id="tenant-a",
                command=task.command,
            )
            context = {
                "granted": True,
                "explicit": True,
                "source": "approval_store",
                "approval_id": issued["approval_id"],
                "approval_scope": "task.execute",
            }
            bot = _Bot(store)
            service = CommandExecutionService(bot)
            self.assertTrue(service._approval_required("different operation"))
            self._assert_gate_denies(service, "different operation", task, context)
            self.assertEqual(bot.route_calls, 0)
        finally:
            store.close()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
