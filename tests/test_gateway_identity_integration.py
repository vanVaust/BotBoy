"""Authentication-on HTTP regressions: claims, live credentials, and tenant scope."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from botboy.__main__ import BotBoy
from botboy.core.config import BotBoyConfig
from botboy.gateway.server import create_app


class GatewayIdentityIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="botboy-identity-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        config = BotBoyConfig()
        for section in ("memory", "history", "scheduler", "trace", "tasks"):
            getattr(config, section).db_path = str(root / f"{section}.db")
        config.tasks.artifact_root = str(root / "artifacts")
        config.security.principal_db_path = str(root / "principals.db")
        config.security.auth_api_key_store_path = str(root / "keys.db")
        config.security.jwt_secret_file = str(root / "jwt.secret")
        config.security.jwt_secret = "test-only-integration-secret-123456789"
        config.security.token_revocation_db_path = str(root / "revocations.db")
        config.security.enable_auth = True
        config.security.rate_limit_enabled = False
        config.scheduler.enabled = False
        self.bot = BotBoy(config)
        self.assertTrue(self.bot.initialize())
        self.addCleanup(self.bot.shutdown)
        self.app = create_app(self.bot)
        self.store = self.app.state.bot_principal_store()
        self.store.upsert_principal("alice", "password", "admin", "tenant-a")
        self.store.upsert_principal("bob", "password", "user", "tenant-b")
        self.store.upsert_principal("root", "password", "system", "platform")
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def login(self, username="alice", **credentials):
        response = self.client.post("/api/auth/login", json=credentials if "api_key" in credentials else {"username": username, "password": credentials.get("password", "password")})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    @staticmethod
    def headers(login):
        return {"Authorization": "Bearer " + login["access_token"]}

    def assert_pair_tenant(self, login, tenant):
        self.assertEqual(login["principal"]["org_id"], tenant)
        auth = self.app.state.bot_auth
        self.assertEqual(auth.verify(login["access_token"]).org_id, tenant)
        self.assertEqual(auth.verify(login["refresh_token"], expected_type="refresh").org_id, tenant)

    def test_password_and_api_key_login_and_refresh_bind_tenant(self):
        key = self.app.state.bot_api_key_store().generate(role="admin", org_id="tenant-a")
        for credentials in ({"username": "alice", "password": "password"}, {"api_key": key.full_key}):
            login = self.login(**credentials)
            self.assert_pair_tenant(login, "tenant-a")
            refresh = self.client.post("/api/auth/refresh", json={"refresh_token": login["refresh_token"]})
            self.assertEqual(refresh.status_code, 200, refresh.text)
            self.assert_pair_tenant(refresh.json(), "tenant-a")
            self.assertEqual(self.client.post("/api/auth/refresh", json={"refresh_token": login["refresh_token"]}).status_code, 401)

    def test_admin_context_preserves_role_and_credential_tenant(self):
        headers = self.headers(self.login())
        self.assertEqual(self.client.get("/api/v2/workers/nodes", headers=headers).status_code, 200)
        issued = self.client.post("/api/auth/api-key", headers=headers, json={"principal_id": "svc-a"})
        self.assertEqual(issued.status_code, 200, issued.text)
        self.assertEqual(issued.json()["principal"]["org_id"], "tenant-a")
        listed = self.client.get("/api/principals", headers=headers).json()
        self.assertEqual([p["username"] for p in listed["principals"]], ["alice"])
        self.assertEqual(listed["principals"][0]["org_id"], "tenant-a")
        self.assertEqual(self.client.post("/api/auth/api-key", headers=headers, json={"principal_id": "svc-b", "org_id": "tenant-b"}).status_code, 403)
        self.assertEqual(self.client.post("/api/auth/api-key", headers=headers, json={"role": "system"}).status_code, 403)

    def test_tenant_admin_cannot_overwrite_or_disable_other_tenant_or_system(self):
        headers = self.headers(self.login())
        self.store.upsert_principal("same-tenant-system", "password", "system", "tenant-a")
        for username in ("bob", "same-tenant-system"):
            mutation = self.client.post("/api/principals", headers=headers, json={"username": username, "password": "attacker-password", "role": "admin"})
            self.assertEqual(mutation.status_code, 404, mutation.text)
            self.assertEqual(self.client.delete("/api/principals/" + username, headers=headers).status_code, 404)
            self.assertIsNotNone(self.store.authenticate(username, "password"))

    def test_system_can_administer_explicit_other_tenant(self):
        headers = self.headers(self.login("root"))
        issued = self.client.post("/api/auth/api-key", headers=headers, json={"principal_id": "system-issued", "org_id": "tenant-b", "role": "system"})
        self.assertEqual(issued.status_code, 200, issued.text)
        self.assertEqual(issued.json()["principal"]["org_id"], "tenant-b")
        updated = self.client.post("/api/principals", headers=headers, json={"username": "bob", "password": "new-password", "org_id": "tenant-b", "role": "admin"})
        self.assertEqual(updated.status_code, 200, updated.text)

    def test_password_mutation_revokes_access_and_refresh(self):
        login = self.login()
        self.store.upsert_principal("alice", "changed-password", "admin", "tenant-a")
        self.assertEqual(self.client.get("/api/principals", headers=self.headers(login)).status_code, 401)
        self.assertEqual(self.client.post("/api/auth/refresh", json={"refresh_token": login["refresh_token"]}).status_code, 401)

    def test_authenticated_app_rejects_unavailable_persistent_revocation_store(self):
        import sqlite3
        from botboy.gateway.simple_server_support import build_simple_handler_state
        for error in (OSError, RuntimeError, sqlite3.OperationalError):
            with self.subTest(error=error.__name__):
                with patch("botboy.gateway.secrets.TokenRevocationStore", side_effect=error("test-only store unavailable")):
                    with self.assertRaisesRegex(RuntimeError, "Persistent token revocation store is unavailable"):
                        create_app(self.bot)
                    with self.assertRaisesRegex(RuntimeError, "Persistent token revocation store is unavailable"):
                        build_simple_handler_state(self.bot, host="127.0.0.1", port=0)

    def test_local_auth_disabled_can_start_without_persistent_revocation_store(self):
        from botboy.gateway.simple_server_support import build_simple_handler_state
        self.bot.config.security.enable_auth = False
        with patch("botboy.gateway.secrets.TokenRevocationStore", side_effect=OSError("test-only store unavailable")):
            app = create_app(self.bot)
            self.assertIsNone(app.state.bot_auth._revocation_store)
            state = build_simple_handler_state(self.bot, host="127.0.0.1", port=0)
            self.assertIsNone(state["auth"]._revocation_store)

    def test_disable_and_api_key_revocation_apply_to_existing_access_tokens(self):
        login = self.login()
        self.store.disable_principal("alice")
        self.assertEqual(self.client.get("/api/v2/workers/nodes", headers=self.headers(login)).status_code, 401)
        key_store = self.app.state.bot_api_key_store()
        key = key_store.generate(role="admin", org_id="tenant-a")
        login = self.login(api_key=key.full_key)
        key_store.revoke(key.key_id)
        self.assertEqual(self.client.get("/api/v2/workers/nodes", headers=self.headers(login)).status_code, 401)
        self.assertEqual(self.client.post("/api/auth/refresh", json={"refresh_token": login["refresh_token"]}).status_code, 401)

    def test_real_command_retains_login_tenant(self):
        login = self.login()
        response = self.client.post("/api/command", headers=self.headers(login), json={"command": "status"})
        self.assertEqual(response.status_code, 200, response.text)
        tasks, total = self.bot.task_store.list_tasks(principal="alice", limit=100)
        self.assertGreater(total, 0)
        self.assertTrue(all(task.org_id == "tenant-a" for task in tasks))
        self.assertTrue(all(self.bot._lifecycle_service().load_security_context(task.task_id).org_id == "tenant-a" for task in tasks))

    def test_task_and_memory_scope_precedes_pagination_and_search(self):
        headers = self.headers(self.login())
        for i in range(3):
            self.bot.task_store.create_task(title=f"own-{i}", command="status", principal="alice", org_id="tenant-a")
            self.bot.memory.store(f"needle own-{i}", {"_botboy_security": {"principal_id": "alice", "org_id": "tenant-a"}})
        for i in range(510):
            self.bot.memory.store(f"needle foreign-{i}", {"_botboy_security": {"principal_id": "bob", "org_id": "tenant-b"}})
        for i in range(4):
            self.bot.task_store.create_task(title=f"foreign-{i}", command="status", principal="bob", org_id="tenant-b")
        response = self.client.get("/api/tasks?limit=2&offset=1", headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["total"], 3)
        self.assertEqual(len(response.json()["tasks"]), 2)
        response = self.client.get("/api/memories?limit=2&offset=1", headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["total"], 3)
        self.assertEqual(len(response.json()["memories"]), 2)
        search = self.client.get("/api/memories/search?q=needle&limit=2", headers=headers)
        self.assertEqual(search.status_code, 200, search.text)
        self.assertEqual(search.json()["count"], 2)
        self.assertTrue(all("own-" in memory["content"] for memory in search.json()["results"]))

    def test_telemetry_requires_system_and_health_has_no_cache_statistics(self):
        admin = self.headers(self.login())
        system = self.headers(self.login("root"))
        for path in ("/metrics", "/api/metrics", "/api/dashboard", "/api/monitoring"):
            self.assertEqual(self.client.get(path, headers=admin).status_code, 403, path)
            self.assertEqual(self.client.get(path, headers=system).status_code, 200, path)
        self.assertNotIn("cache", self.client.get("/health").json())

    def test_node_registration_preserves_server_identity_and_prevents_takeover(self):
        headers = self.headers(self.login())
        payload = {"node_id": "node-a", "worker_id": "executor", "queue_name": "queue-a",
                   "metadata": {"org_id": "tenant-b", "owner_principal": "bob"}}
        response = self.client.post("/api/v2/workers/register", headers=headers, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        node = self.bot.task_store.get_worker_node("node-a")
        self.assertEqual(node["metadata"]["org_id"], "tenant-a")
        self.assertEqual(node["metadata"]["owner_principal"], "alice")
        queue = self.bot.task_store.get_execution_queue("queue-a")
        self.assertEqual(queue["metadata"]["org_id"], "tenant-a")
        heartbeat = self.client.post("/api/v2/workers/heartbeat", headers=headers, json={"node_id": "node-a", "metadata": {"org_id": "tenant-b", "owner_principal": "bob"}})
        self.assertEqual(heartbeat.status_code, 200, heartbeat.text)
        self.assertEqual(self.bot.task_store.get_worker_node("node-a")["metadata"]["org_id"], "tenant-a")
        self.assertEqual(self.bot.task_store.get_worker_node("node-a")["metadata"]["owner_principal"], "alice")
        self.bot.task_store.register_worker_node(node_id="node-b", worker_id="executor", queue_name="queue-b", metadata={"org_id": "tenant-b", "owner_principal": "bob"})
        for node_id, queue_name in (("node-b", "queue-c"), ("node-c", "queue-b")):
            response = self.client.post("/api/v2/workers/register", headers=headers, json={"node_id": node_id, "worker_id": "executor", "queue_name": queue_name})
            self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(self.bot.task_store.get_worker_node("node-b")["metadata"]["org_id"], "tenant-b")
        # Fleet API passes a raw authorization string; it must establish the same context.
        response = self.client.get("/api/v1/fleet/workers", headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([node["node_id"] for node in response.json()], ["node-a"])

    def test_same_principal_foreign_lease_is_hidden(self):
        self.bot.task_store.register_worker_node(node_id="node-b", worker_id="executor", queue_name="queue-b", metadata={"org_id": "tenant-b", "owner_principal": "alice"})
        lease = self.bot.task_store.acquire_queue_lease(node_id="node-b", queue_name="queue-b", metadata={"org_id": "tenant-b", "principal": "alice"})
        self.assertIsNotNone(lease)
        response = self.client.get("/api/v2/workers/leases", headers=self.headers(self.login()))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["leases"], [])

    def test_same_principal_foreign_trace_is_hidden(self):
        foreign = self.bot.task_store.create_task(title="foreign", command="status", principal="alice", org_id="tenant-b")
        run = self.bot.trace_store.create_run(command="status", principal="alice", task_id=foreign.task_id)
        headers = self.headers(self.login())
        response = self.client.get("/api/traces/" + run.run_id, headers=headers)
        self.assertEqual(response.status_code, 404, response.text)
        response = self.client.get("/api/traces", headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["total"], 0)

    def test_invalid_auth_input_is_rejected_without_server_errors(self):
        for payload in ([], {"username": []}, {"api_key": 123}, {"username": "alice", "password": {}}):
            response = self.client.post("/api/auth/login", json=payload)
            self.assertEqual(response.status_code, 400, response.text)
        headers = self.headers(self.login())
        for value in ("NaN", "inf", 0, -1):
            response = self.client.post("/api/auth/api-key", headers=headers, json={"principal_id": "bad-ttl", "ttl_hours": value})
            self.assertEqual(response.status_code, 400, response.text)

    def test_history_routes_are_unique_and_record_endpoint_enforces_tenant(self):
        import warnings
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            schema = self.app.openapi()
        for path in ("/api/history", "/api/history/stats", "/api/history/{record_id}"):
            self.assertIn("get", schema["paths"][path])
        self.assertFalse(any("Duplicate Operation ID" in str(item.message) for item in captured))
        own = self.bot.history.record("own", principal="alice", org_id="tenant-a")
        foreign = self.bot.history.record("foreign", principal="bob", org_id="tenant-b")
        headers = self.headers(self.login())
        self.assertEqual(self.client.get(f"/api/history/{own}", headers=headers).status_code, 200)
        self.assertEqual(self.client.get(f"/api/history/{foreign}", headers=headers).status_code, 404)
        self.assertEqual(self.client.get(f"/api/history/{own}").status_code, 401)
        system = self.headers(self.login("root"))
        self.assertEqual(self.client.get("/api/history", headers=system).json()["total"], 2)

    def test_application_registers_explicit_forwarded_header_guard(self):
        from fastapi import Request
        from botboy.gateway.security import TrustedProxyHeaderMiddleware
        self.assertTrue(any(middleware.cls is TrustedProxyHeaderMiddleware for middleware in self.app.user_middleware))
        async def inspect_headers(request: Request):
            return {"forwarded": request.headers.get("x-forwarded-for")}
        self.app.add_api_route("/test-only/proxy-headers", inspect_headers)
        response = self.client.get("/test-only/proxy-headers", headers={"X-Forwarded-For": "203.0.113.99"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()["forwarded"])
