"""Authenticated HTTP-to-effect regressions using real SQLite persistence."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from botboy.tasks import TaskStore
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock

from fastapi.testclient import TestClient

from botboy.__main__ import BotBoy
from botboy.approval_store import ApprovalStore
from botboy.command_execution_service import CommandExecutionService
from botboy.core.config import BotBoyConfig
from botboy.gateway.server import create_app
from botboy.security_authorization import AuthorizationError
from botboy.security_context import SecurityContext
from botboy.security_policy import DEFAULT_CAPABILITY_POLICY
from botboy.task_security import TaskSecurityStore


class ExecutionTenantApprovalIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        config = BotBoyConfig()
        for section in ("memory", "history", "scheduler", "trace", "tasks"):
            getattr(config, section).db_path = str(root / (section + ".db"))
        config.tasks.artifact_root = str(root / "artifacts")
        config.security.principal_db_path = str(root / "principals.db")
        config.security.auth_api_key_store_path = str(root / "keys.db")
        config.security.jwt_secret_file = str(root / "jwt.secret")
        config.security.jwt_secret = "integration-only-local-test-secret-123456"
        config.security.enable_auth = True
        config.llm.enabled = False
        self.bot = BotBoy(config)
        self.assertTrue(self.bot.initialize())
        self.app = create_app(self.bot, host="127.0.0.1", port=8765)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.headers = {"Authorization": "Bearer " + self.app.state.bot_auth.create_pair(
            "alice", ["user"], org_id="tenant-a").access_token}
        self.context = SecurityContext(principal_id="alice", org_id="tenant-a",
                                       roles=frozenset({"user"}), auth_source="fastapi",
                                       capabilities=DEFAULT_CAPABILITY_POLICY.user)

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.bot.shutdown()
        self.tmp.cleanup()

    def _task(self, command="dangerous operation"):
        return self.bot.lifecycle_service.create_task_context(
            command, principal="alice", request_id="approval-test", org_id="tenant-a",
            security_context=self.context,
        )

    def _gated_skill(self):
        self.bot.skills.get_contract_for_command = lambda command: {"needs_approval": command == "dangerous operation"}

    def test_http_command_persists_tenant_context_and_history(self):
        response = self.client.post("/api/command", headers=self.headers, json={"command": "status"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["success"])
        task_id = response.json()["data"]["task"]["task_id"]
        record = self.bot.task_store.get_task(task_id)
        self.assertEqual((record.principal, record.org_id), ("alice", "tenant-a"))
        persisted = self.bot.lifecycle_service.load_security_context(task_id)
        self.assertEqual(persisted.org_id, "tenant-a")
        self.assertIn("task.execute", persisted.capabilities)
        history, total = self.bot.history.list(task_id=task_id, org_id="tenant-a")
        self.assertEqual(total, 1)
        self.assertEqual(history[0].principal, "alice")

    def test_remote_legacy_commands_cannot_bypass_scoped_apis(self):
        foreign = self.bot.task_store.create_task(title="foreign", principal="bob", org_id="tenant-b", command="status")
        for command in (f"task show {foreign.task_id}", f"task cancel {foreign.task_id}",
                        "task list", "history", "memories", "worker list", "schedule list",
                        "system info", "plan read all memory", "hsearch secret"):
            with self.subTest(command=command):
                response = self.client.post("/api/command", headers=self.headers, json={"command": command})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["type"], "authorization_error")
        self.assertEqual(self.bot.task_store.get_task(foreign.task_id).status, "queued")

    def test_http_approval_resume_executes_once_and_replay_is_denied(self):
        self._gated_skill()
        task = self._task()
        self.bot.task_store.update_status(task.task_id, status="waiting_approval", principal="alice")
        self.bot._route = AsyncMock(return_value={"success": True, "output": "effect", "type": "test"})
        issued = self.client.post(f"/api/tasks/{task.task_id}/approve", headers=self.headers)
        self.assertEqual(issued.status_code, 200, issued.text)
        approval_id = issued.json()["approval"]["approval_id"]
        headers = {**self.headers, "x-botboy-approval": approval_id}
        resumed = self.client.post(f"/api/tasks/{task.task_id}/resume", headers=headers)
        self.assertEqual(resumed.status_code, 200, resumed.text)
        self.assertTrue(resumed.json()["success"], resumed.text)
        self.assertEqual(self.bot._route.await_count, 1)
        # Even resetting task status cannot turn a used grant into another effect.
        self.bot.task_store.update_status(task.task_id, status="waiting_approval", principal="alice")
        replay = self.client.post(f"/api/tasks/{task.task_id}/resume", headers=headers)
        self.assertEqual(replay.status_code, 403)
        self.assertEqual(self.bot._route.await_count, 1)

    def test_consumed_grant_is_claimed_once_even_without_gateway_import_hook(self):
        self._gated_skill()
        task = self._task()
        approvals = ApprovalStore(self.bot.task_store)
        grant = approvals.issue(task_id=task.task_id, principal_id="alice", org_id="tenant-a", command=task.command)
        approvals.consume_if_valid(grant["approval_id"], task_id=task.task_id, principal_id="alice", org_id="tenant-a", command=task.command)
        context = {"granted": True, "explicit": True, "source": "approval_store",
                   "approval_id": grant["approval_id"], "approval_scope": "task.execute"}
        self.bot._route = AsyncMock(return_value={"success": True, "output": "effect", "type": "test"})
        async def execute():
            return await self.bot.process_command(task.command, security_context=self.context,
                                                  task_context=task, approval_context=context)
        self.assertTrue(asyncio.run(execute())["success"])
        self.assertEqual(asyncio.run(execute())["type"], "authorization_error")
        self.assertEqual(self.bot._route.await_count, 1)

    def test_grant_bindings_reject_every_mismatch_without_burning_valid_grant(self):
        task = self._task()
        approvals = ApprovalStore(self.bot.task_store)
        grant = approvals.issue(task_id=task.task_id, principal_id="alice", org_id="tenant-a", command=task.command, authorization_version=7)
        good = dict(task_id=task.task_id, principal_id="alice", org_id="tenant-a", command=task.command,
                    capability="task.execute", authorization_version=7)
        for key, value in (("task_id", "different-task"), ("principal_id", "bob"), ("org_id", "tenant-b"),
                           ("command", "different command"), ("capability", "task.cancel"), ("authorization_version", 8)):
            with self.subTest(binding=key):
                self.assertIsNone(approvals.consume_if_valid(grant["approval_id"], **{**good, key: value}))
        self.assertIsNotNone(approvals.consume_if_valid(grant["approval_id"], **good))
        for key, value in (("task_id", "different-task"), ("principal_id", "bob"), ("org_id", "tenant-b"),
                           ("command", "different command"), ("capability", "task.cancel"), ("authorization_version", 8)):
            with self.subTest(final_binding=key):
                self.assertIsNone(approvals.claim_execution_if_valid(grant["approval_id"], **{**good, key: value}))
        self.assertIsNotNone(approvals.claim_execution_if_valid(grant["approval_id"], **good))
        self.assertIsNone(approvals.claim_execution_if_valid(grant["approval_id"], **good))

    def test_expiry_between_consume_and_final_effect_blocks_execution(self):
        task = self._task()
        approvals = ApprovalStore(self.bot.task_store)
        grant = approvals.issue(task_id=task.task_id, principal_id="alice", org_id="tenant-a", command=task.command)
        args = dict(task_id=task.task_id, principal_id="alice", org_id="tenant-a", command=task.command)
        self.assertIsNotNone(approvals.consume_if_valid(grant["approval_id"], **args))
        conn = self.bot.task_store._get_conn()
        conn.execute("UPDATE task_approvals SET expires_at = ? WHERE approval_id = ?", ("2000-01-01T00:00:00+00:00", grant["approval_id"]))
        conn.commit()
        self.assertIsNone(approvals.claim_execution_if_valid(grant["approval_id"], **args))

    def test_task_identity_version_and_capability_checks_precede_effect(self):
        task = self._task("status")
        self.bot._route = AsyncMock(return_value={"success": True, "output": "effect", "type": "test"})
        for altered in (replace(self.context, principal_id="bob"), replace(self.context, org_id="tenant-b"),
                        replace(self.context, task_id="another-task"), replace(self.context, authorization_version="2"),
                        replace(self.context, capabilities=frozenset())):
            with self.subTest(context=altered):
                result = asyncio.run(self.bot.process_command("status", task_context=task, security_context=altered))
                self.assertEqual(result["type"], "authorization_error", result)
        self.assertEqual(self.bot._route.await_count, 0)

    def test_concurrent_final_claim_has_one_winner(self):
        task = self._task()
        approvals = ApprovalStore(self.bot.task_store)
        grant = approvals.issue(task_id=task.task_id, principal_id="alice", org_id="tenant-a", command=task.command)
        args = dict(task_id=task.task_id, principal_id="alice", org_id="tenant-a", command=task.command)
        approvals.consume_if_valid(grant["approval_id"], **args)
        # Twelve independently constructed stores and twelve live connections,
        # synchronized at the UPDATE boundary: no shared-connection false proof.
        stores = [TaskStore(db_path=self.bot.task_store.db_path) for _ in range(12)]
        gate = Barrier(12)
        try:
            def claim(index):
                store = stores[index]
                approvals = ApprovalStore(store)
                connection = store._get_conn()
                gate.wait(timeout=10)
                return connection, approvals.claim_execution_if_valid(grant["approval_id"], **args)
            with ThreadPoolExecutor(max_workers=12) as pool:
                results = list(pool.map(claim, range(12)))
            self.assertEqual(len({id(connection) for connection, _ in results}), 12)
            self.assertEqual(sum(result is not None for _, result in results), 1)
        finally:
            for store in stores:
                store.close()

    def test_stdlib_issue_resume_uses_the_same_server_grant(self):
        from botboy.gateway.simple_task_write_handlers import handle_task_approve, handle_task_resume
        from botboy.gateway.simple_routing import resolve_post_route
        self.assertEqual(resolve_post_route("/api/tasks/owned/approve"), ("_handle_task_approve", "owned"))
        self._gated_skill()
        task = self._task()
        self.bot.task_store.update_status(task.task_id, status="waiting_approval", principal="alice")
        responses = []
        handler = SimpleNamespace(
            botboy=self.bot, auth_enabled=True, request_id="stdlib-test", headers={},
            _ensure_access_with_roles=lambda **kw: ("alice", ["user"]),
            _task_store=lambda: self.bot.task_store,
            _support=lambda: SimpleNamespace(security_context=lambda: replace(self.context, auth_source="stdlib")),
            _json=lambda body, status=200: responses.append((body, status)),
        )
        self.bot._route = AsyncMock(return_value={"success": True, "output": "effect", "type": "test"})
        handle_task_resume(handler, task.task_id, {"approval": True})
        self.assertEqual(responses[-1][1], 403)
        handle_task_approve(handler, task.task_id, {})
        self.assertEqual(responses[-1][1], 200, responses[-1])
        handler.headers["x-botboy-approval"] = responses[-1][0]["approval"]["approval_id"]
        handle_task_resume(handler, task.task_id, {})
        self.assertEqual(responses[-1][1], 200, responses[-1])
        self.assertTrue(responses[-1][0]["success"], responses[-1])
        self.bot.task_store.update_status(task.task_id, status="waiting_approval", principal="alice")
        handle_task_resume(handler, task.task_id, {})
        self.assertEqual(responses[-1][1], 403)
        self.assertEqual(self.bot._route.await_count, 1)

    def test_remote_status_cannot_trigger_global_maintenance(self):
        self.bot.self_healing = Mock()
        self.bot.dag_engine = Mock()
        response = self.client.post("/api/command", headers=self.headers, json={"command": "status"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        self.bot.self_healing.scan_for_stale_tasks.assert_not_called()
        self.bot.dag_engine.execute_fringe.assert_not_called()

    def test_history_detail_authenticates_and_keeps_admin_within_tenant(self):
        record_id = self.bot.history.record(command="secret-b", output="private", principal="bob", org_id="tenant-b")
        self.assertEqual(self.client.get(f"/api/history/{record_id}").status_code, 401)
        admin_headers = {"Authorization": "Bearer " + self.app.state.bot_auth.create_pair(
            "alice", ["admin"], org_id="tenant-a").access_token}
        self.assertEqual(self.client.get(f"/api/history/{record_id}", headers=admin_headers).status_code, 404)
        own_id = self.bot.history.record(command="owned", output="ok", principal="alice", org_id="tenant-a")
        own = self.client.get(f"/api/history/{own_id}", headers=self.headers)
        self.assertEqual(own.status_code, 200, own.text)
        self.assertEqual(own.json()["org_id"], "tenant-a")

    def test_cache_scope_uses_unambiguous_full_authority(self):
        scope = CommandExecutionService._cache_security_scope
        self.assertNotEqual(scope(replace(self.context, org_id="a|principal=b", principal_id="c")),
                            scope(replace(self.context, org_id="a", principal_id="b|principal=c")))
        self.assertNotEqual(scope(self.context), scope(replace(self.context, capabilities=frozenset())))
        self.assertNotEqual(scope(self.context), scope(replace(self.context, auth_source="local")))


if __name__ == "__main__":
    unittest.main()
