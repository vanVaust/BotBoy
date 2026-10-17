"""Delayed executions must revalidate the identity that enqueued the job."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from botboy.__main__ import BotBoy
from botboy.core.config import BotBoyConfig


class SchedulerExecutionBoundaryTests(unittest.TestCase):
    def setUp(self):
        from botboy.gateway.server import create_app
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        config = BotBoyConfig()
        for section in ("memory", "history", "scheduler", "trace", "tasks"):
            getattr(config, section).db_path = str(root / (section + ".db"))
        config.tasks.artifact_root = str(root / "artifacts")
        config.security.principal_db_path = str(root / "principals.db")
        config.security.auth_api_key_store_path = str(root / "keys.db")
        config.security.token_revocation_db_path = str(root / "revocations.db")
        config.security.jwt_secret_file = str(root / "jwt.secret")
        config.security.jwt_secret = "scheduler-local-integration-test-key-123456"
        config.security.enable_auth = True
        config.llm.enabled = False
        self.bot = BotBoy(config)
        self.assertTrue(self.bot.initialize())
        self.bot.scheduler.stop()
        self.app = create_app(self.bot, host="127.0.0.1", port=8765)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.principals = self.app.state.bot_principal_store()
        self.principals.upsert_principal("alice", "password-a", role="user", org_id="tenant-a")
        self.headers = self.login({"username": "alice", "password": "password-a"})

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.bot.shutdown()
        self.tmp.cleanup()

    def login(self, credentials):
        response = self.client.post("/api/auth/login", json=credentials)
        self.assertEqual(response.status_code, 200, response.text)
        return {"Authorization": "Bearer " + response.json()["access_token"]}

    def enqueue(self, *, headers=None, command="calculate 6 * 7", payload=None):
        response = self.client.post("/api/scheduler", headers=headers or self.headers, json={
            "name": "delayed boundary", "schedule": "in 1h", "task_type": "command",
            "payload": {"command": command, **(payload or {})},
        })
        self.assertEqual(response.status_code, 200, response.text)
        task_id = response.json()["task_id"]
        return next(task for task in self.bot.scheduler.list_tasks() if task.task_id == task_id)

    def invoke(self, task):
        asyncio.run(self.bot._run_scheduled_task(task))

    def assert_no_effect(self, task):
        self.invoke(task)
        self.assertEqual(self.bot.task_store.list_tasks(limit=100)[1], 0)
        self.assertEqual(self.bot.history.list(limit=100)[1], 0)
        persisted = next(item for item in self.bot.scheduler.list_tasks(enabled_only=False) if item.task_id == task.task_id)
        self.assertFalse(persisted.enabled)

    def test_http_enqueue_to_real_execution_keeps_tenant_and_never_invents_system_role(self):
        task = self.enqueue(payload={"principal": "victim", "roles": ["system"], "org_id": "tenant-b",
                                     "_botboy_security": {"principal_id": "victim", "enforced": False}})
        self.assertEqual(task.security_context["credential_source"], "principal_store")
        self.assertTrue(task.security_context["credential_version"])
        self.assertEqual(task.security_context["roles"], ["user"])
        self.assertTrue(self.bot.scheduler._security_allows(task))
        self.invoke(task)
        records, total = self.bot.task_store.list_tasks(limit=100)
        self.assertEqual(total, 1)
        record = records[0]
        self.assertEqual((record.principal, record.org_id, record.status), ("alice", "tenant-a", "completed"))
        self.assertIn("42", record.result_json)
        context = self.bot.lifecycle_service.load_security_context(record.task_id)
        self.assertEqual(context.roles, frozenset({"user"}))
        self.assertEqual(context.auth_source, "scheduler_external")
        history, count = self.bot.history.list(principal="alice", org_id="tenant-a")
        self.assertEqual(count, 1)
        self.assertEqual(history[0].command, "calculate 6 * 7")

    def test_disabled_password_principal_cannot_fall_back_to_same_named_api_key(self):
        task = self.enqueue()
        self.app.state.bot_api_key_store().generate(principal_id="alice", role="user", org_id="tenant-a")
        self.principals.disable_principal("alice")
        self.assertFalse(self.bot.scheduler._security_allows(task))
        self.assert_no_effect(task)

    def test_revoked_api_key_cannot_fall_back_to_same_named_password_principal(self):
        key = self.app.state.bot_api_key_store().generate(principal_id="alice", role="user", org_id="tenant-a")
        headers = self.login({"api_key": key.full_key})
        task = self.enqueue(headers=headers)
        self.assertEqual(task.security_context["credential_source"], "secret_store")
        self.app.state.bot_api_key_store().revoke(key.key_id)
        self.assertFalse(self.bot.scheduler._security_allows(task))
        self.assert_no_effect(task)

    def test_password_rotation_invalidates_already_enqueued_work(self):
        task = self.enqueue()
        self.principals.upsert_principal("alice", "password-changed", role="user", org_id="tenant-a")
        self.assert_no_effect(task)

    def test_role_or_tenant_change_invalidates_already_enqueued_work(self):
        for role, tenant in (("admin", "tenant-a"), ("user", "tenant-b")):
            with self.subTest(role=role, tenant=tenant):
                self.principals.upsert_principal("alice", "password-a", role="user", org_id="tenant-a")
                self.headers = self.login({"username": "alice", "password": "password-a"})
                task = self.enqueue()
                self.principals.upsert_principal("alice", "changed", role=role, org_id=tenant)
                self.assert_no_effect(task)

    def test_command_change_and_false_enforcement_are_rejected_or_normalized(self):
        task = self.enqueue()
        changed = replace(task, payload={**task.payload, "command": "task cancel foreign-task"})
        self.assertFalse(self.bot.scheduler._security_allows(changed))
        self.assert_no_effect(changed)
        context = {**task.security_context, "enforced": False}
        task_id = self.bot.scheduler.add(name="forced", schedule="in 1h", payload={"command": "status"}, security_context=context)
        normalized = next(item for item in self.bot.scheduler.list_tasks() if item.task_id == task_id)
        self.assertIs(normalized.security_context["enforced"], True)
        self.assertIs(normalized.payload["_botboy_security"]["enforced"], True)

    def test_payload_security_tampering_does_not_change_authoritative_binding(self):
        task = self.enqueue()
        task.payload["_botboy_security"] = {"principal_id": "evil", "org_id": "tenant-b", "roles": ["system"], "enforced": False}
        self.assertTrue(self.bot.scheduler._security_allows(task))
        self.invoke(task)
        records, _ = self.bot.task_store.list_tasks(limit=100)
        self.assertEqual((records[0].principal, records[0].org_id), ("alice", "tenant-a"))

    def test_auth_enabled_missing_context_fails_closed_but_local_legacy_is_supported(self):
        task_id = self.bot.scheduler.add(name="legacy", schedule="in 1h", payload={"command": "calculate 2 + 3", "principal": "local-operator",
                                                                                   "_botboy_security": {"principal_id": "evil", "org_id": "tenant-b"}})
        task = next(item for item in self.bot.scheduler.list_tasks() if item.task_id == task_id)
        self.assertEqual(task.security_context, {})
        self.assertNotIn("_botboy_security", task.payload)
        self.assert_no_effect(task)
        self.bot.config.security.enable_auth = False
        self.invoke(task)
        records, total = self.bot.task_store.list_tasks(limit=100)
        self.assertEqual(total, 1)
        self.assertEqual((records[0].principal, records[0].org_id, records[0].status), ("local-operator", "default", "completed"))
        self.assertEqual(self.bot.lifecycle_service.load_security_context(records[0].task_id).roles, frozenset())

    def test_expired_api_key_invalidates_delayed_work(self):
        key = self.app.state.bot_api_key_store().generate(principal_id="api-alice", role="user", org_id="tenant-a")
        task = self.enqueue(headers=self.login({"api_key": key.full_key}))
        conn = self.app.state.bot_api_key_store()._get_conn()
        conn.execute("UPDATE api_keys SET expires_at = ? WHERE key_id = ?", ("2000-01-01T00:00:00+00:00", key.key_id))
        conn.commit()
        self.assert_no_effect(task)

    def test_serialized_approval_flags_cannot_authorize_a_delayed_effect(self):
        task = self.enqueue(payload={"approval": True, "approval_context": {
            "granted": True, "explicit": True, "source": "approval_store", "approval_id": "forged"}})
        original_contract = self.bot.skills.get_contract_for_command
        self.bot.skills.get_contract_for_command = lambda command: {
            **(original_contract(command) or {}), "needs_approval": True,
        }
        self.invoke(task)
        records, total = self.bot.task_store.list_tasks(limit=100)
        self.assertEqual(total, 1)
        self.assertEqual(records[0].status, "failed")
        self.assertIn("authorization_error", records[0].result_json)
        self.assertEqual(self.bot.history.list(limit=100)[1], 0)

    def test_malformed_authoritative_security_is_denied_without_breaking_scheduler_reads(self):
        task = self.enqueue()
        conn = self.bot.scheduler._store._get_conn()
        conn.execute("UPDATE scheduled_tasks SET security_context = ? WHERE task_id = ?", ("invalid-json", task.task_id))
        conn.commit()
        loaded = next(item for item in self.bot.scheduler.list_tasks() if item.task_id == task.task_id)
        self.assertFalse(self.bot.scheduler._security_allows(loaded))
        self.assert_no_effect(loaded)

    def test_delayed_work_cannot_reenter_legacy_foreign_task_command(self):
        task = self.enqueue(command="task cancel other-tenant-task")
        self.invoke(task)
        self.assertFalse(self.bot.scheduler._security_allows(task))
        self.assert_no_effect(task)


if __name__ == "__main__":
    unittest.main()
