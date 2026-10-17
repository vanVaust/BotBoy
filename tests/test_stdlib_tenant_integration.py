"""Real HTTP tests for the stdlib gateway with authentication enabled."""
import http.client
import json
from pathlib import Path
import tempfile
import unittest

from botboy.__main__ import BotBoy
from botboy.core.config import BotBoyConfig
from botboy.gateway.secrets import PrincipalStore
from botboy.gateway.simple_server import SimpleHTTPServer


class StdlibTenantIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="botboy-stdlib-identity-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        config = BotBoyConfig()
        for section in ("memory", "history", "scheduler", "trace", "tasks"):
            getattr(config, section).db_path = str(root / f"{section}.db")
        config.tasks.artifact_root = str(root / "artifacts")
        config.security.principal_db_path = str(root / "principals.db")
        config.security.auth_api_key_store_path = str(root / "keys.db")
        config.security.jwt_secret_file = str(root / "jwt.secret")
        config.security.jwt_secret = "stdlib-test-only-secret-12345678912345"
        config.security.token_revocation_db_path = str(root / "revocations.db")
        config.security.enable_auth = True
        config.security.rate_limit_enabled = False
        config.scheduler.enabled = False
        self.bot = BotBoy(config)
        self.assertTrue(self.bot.initialize())
        self.addCleanup(self.bot.shutdown)
        self.store = PrincipalStore(config.security.principal_db_path)
        self.addCleanup(self.store.close)
        for username, role, tenant in (("alice", "admin", "tenant-a"), ("bob", "user", "tenant-b"), ("root", "system", "platform")):
            self.store.upsert_principal(username, "password", role, tenant)
        self.server = SimpleHTTPServer(self.bot, host="127.0.0.1", port=0)
        self.server.start(blocking=False)
        self.addCleanup(self.server.stop)

    def request(self, method, path, body=None, login=None, extra_headers=None):
        connection = http.client.HTTPConnection(self.server.host, self.server.port, timeout=10)
        headers = {"Content-Type": "application/json"}
        if login:
            headers["Authorization"] = "Bearer " + login["access_token"]
        headers.update(extra_headers or {})
        try:
            connection.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
            response = connection.getresponse()
            data = response.read().decode()
            try:
                data = json.loads(data)
            except ValueError:
                pass
            return response.status, data
        finally:
            connection.close()

    def login(self, username="alice", **credentials):
        status, body = self.request("POST", "/api/auth/login", credentials or {"username": username, "password": "password"})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["principal"]["org_id"], "platform" if username == "root" else "tenant-a")
        return body

    def test_login_refresh_retains_org_and_rotates_refresh_token(self):
        login = self.login()
        status, refreshed = self.request("POST", "/api/auth/refresh", {"refresh_token": login["refresh_token"]})
        self.assertEqual(status, 200, refreshed)
        self.assertEqual(refreshed["principal"]["org_id"], "tenant-a")
        self.assertIn("refresh_token", refreshed)
        status, body = self.request("POST", "/api/auth/refresh", {"refresh_token": login["refresh_token"]})
        self.assertEqual(status, 401, body)

    def test_admin_issuance_and_principal_mutation_are_tenant_scoped(self):
        login = self.login()
        status, issued = self.request("POST", "/api/auth/api-key", {"principal_id": "svc-a"}, login)
        self.assertEqual(status, 200, issued)
        self.assertEqual(issued["principal"]["org_id"], "tenant-a")
        key_login = self.login(api_key=issued["full_key"])
        self.assertEqual(key_login["principal"]["org_id"], "tenant-a")
        for payload in ({"principal_id": "cross-org", "org_id": "tenant-b"}, {"principal_id": "root-escalation", "role": "system"}):
            status, body = self.request("POST", "/api/auth/api-key", payload, login)
            self.assertEqual(status, 403, body)
        status, body = self.request("GET", "/api/principals", login=login)
        self.assertEqual(status, 200, body)
        self.assertEqual([item["username"] for item in body["principals"]], ["alice"])
        status, body = self.request("POST", "/api/principals", {"username": "bob", "password": "attacker-password", "role": "admin"}, login)
        self.assertEqual(status, 404, body)
        status, body = self.request("DELETE", "/api/principals/bob", login=login)
        self.assertEqual(status, 404, body)
        self.assertIsNotNone(self.store.authenticate("bob", "password"))

    def test_password_reset_revokes_existing_access_and_refresh(self):
        login = self.login()
        self.store.upsert_principal("alice", "new-password", "admin", "tenant-a")
        status, body = self.request("GET", "/api/principals", login=login)
        self.assertEqual(status, 401, body)
        status, body = self.request("POST", "/api/auth/refresh", {"refresh_token": login["refresh_token"]})
        self.assertEqual(status, 401, body)

    def test_command_tasks_history_and_status_keep_tenant_boundary(self):
        login = self.login()
        status, body = self.request("POST", "/api/command", {"command": "status"}, login)
        self.assertEqual(status, 200, body)
        tasks, total = self.bot.task_store.list_tasks(principal="alice")
        self.assertGreater(total, 0)
        self.assertTrue(all(task.org_id == "tenant-a" for task in tasks))
        self.assertNotIn("Tasks tracked:", body.get("output", ""))
        self.assertNotIn("Memories stored:", body.get("output", ""))
        foreign = self.bot.task_store.create_task(title="foreign", command="status", principal="bob", org_id="tenant-b")
        status, body = self.request("GET", "/api/tasks/" + foreign.task_id, login=login)
        self.assertEqual(status, 404, body)
        status, body = self.request("GET", "/api/history", login=login)
        self.assertEqual(status, 200, body)
        self.assertTrue(all(item["org_id"] == "tenant-a" for item in body["records"]))

    def test_memory_and_telemetry_do_not_expose_other_tenants(self):
        login = self.login()
        for tenant in ("tenant-a", "tenant-b"):
            status, body = self.request("POST", "/api/memories", {"content": "needle own", "metadata": {"_botboy_security": {"principal_id": "bob", "org_id": tenant}}}, login)
            self.assertEqual(status, 200, body)
        for i in range(510):
            self.bot.memory.store("needle foreign", {"_botboy_security": {"principal_id": "bob", "org_id": "tenant-b"}})
        status, body = self.request("GET", "/api/memories?limit=1&offset=1", login=login)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["total"], 2)
        self.assertEqual(len(body["memories"]), 1)
        status, body = self.request("GET", "/api/memories/search?q=needle&limit=2", login=login)
        self.assertEqual(status, 200, body)
        self.assertEqual(len(body["results"]), 2)
        for path in ("/metrics", "/api/metrics", "/api/dashboard", "/api/monitoring"):
            status, body = self.request("GET", path, login=login)
            self.assertEqual(status, 403, (path, body))
        status, body = self.request("GET", "/health")
        self.assertEqual(status, 200, body)
        self.assertNotIn("cache", body)

    def test_system_can_issue_other_tenant_credentials(self):
        login = self.login("root")
        status, body = self.request("POST", "/api/auth/api-key", {"principal_id": "system-key", "org_id": "tenant-b", "role": "system"}, login)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["principal"]["org_id"], "tenant-b")

    def test_same_principal_foreign_trace_and_history_are_hidden(self):
        login = self.login()
        foreign = self.bot.task_store.create_task(title="foreign", command="status", principal="alice", org_id="tenant-b")
        run = self.bot.trace_store.create_run(command="status", principal="alice", task_id=foreign.task_id)
        self.bot.history.record("secret foreign", output="foreign output", principal="alice", org_id="tenant-b")
        self.bot.history.record("own", output="own output", principal="alice", org_id="tenant-a")
        status, body = self.request("GET", "/api/traces/" + run.run_id, login=login)
        self.assertEqual(status, 404, body)
        status, body = self.request("GET", "/api/traces", login=login)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["total"], 0)
        status, body = self.request("GET", "/api/history", login=login)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["records"][0]["command"], "own")
        status, body = self.request("GET", "/api/history")
        self.assertEqual(status, 401, body)

    def test_approval_resume_reaches_effect_once_and_rejects_replay(self):
        from unittest.mock import AsyncMock
        from botboy.security_context import SecurityContext
        from botboy.security_policy import DEFAULT_CAPABILITY_POLICY
        login = self.login()
        context = SecurityContext(principal_id="alice", org_id="tenant-a", roles=frozenset({"admin"}),
                                  capabilities=DEFAULT_CAPABILITY_POLICY.admin, auth_source="stdlib")
        task = self.bot.lifecycle_service.create_task_context("dangerous operation", principal="alice", request_id="stdlib-approval", org_id="tenant-a", security_context=context)
        self.bot.task_store.update_status(task.task_id, status="waiting_approval", principal="alice")
        self.bot.skills.get_contract_for_command = lambda command: {"needs_approval": command == "dangerous operation"}
        self.bot._route = AsyncMock(return_value={"success": True, "output": "effect", "type": "test"})
        status, issued = self.request("POST", f"/api/tasks/{task.task_id}/approve", {}, login)
        self.assertEqual(status, 200, issued)
        approval = {"X-BotBoy-Approval": issued["approval"]["approval_id"]}
        status, body = self.request("POST", f"/api/tasks/{task.task_id}/resume", {}, login, approval)
        self.assertEqual(status, 200, body)
        self.assertTrue(body["success"], body)
        self.assertEqual(self.bot._route.await_count, 1)
        self.bot.task_store.update_status(task.task_id, status="waiting_approval", principal="alice")
        status, body = self.request("POST", f"/api/tasks/{task.task_id}/resume", {}, login, approval)
        self.assertEqual(status, 403, body)
        self.assertEqual(self.bot._route.await_count, 1)
