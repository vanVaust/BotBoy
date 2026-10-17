"""Exercise HTTP MCP tool boundaries against real tenant-scoped SQLite stores."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import botboy_mcp_server as mcp
from botboy.__main__ import BotBoy
from botboy.core.config import BotBoyConfig
from botboy.gateway.app_context import _current_org, _current_principal, _current_roles
from botboy.security_context import SecurityContext
from botboy.security_policy import DEFAULT_CAPABILITY_POLICY


class MCPTenantBoundaryTests(unittest.TestCase):
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
        config.security.jwt_secret = "mcp-integration-only-local-secret-123456"
        config.security.enable_auth = True
        config.llm.enabled = False
        self.bot = BotBoy(config)
        self.assertTrue(self.bot.initialize())
        self.patch_bot = patch.object(mcp, "_bot", self.bot)
        self.patch_bot.start()
        self.addCleanup(self.patch_bot.stop)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.bot.shutdown)
        self.context = SecurityContext(principal_id="mcp-http", org_id="tenant-a",
                                       roles=frozenset({"user"}),
                                       capabilities=DEFAULT_CAPABILITY_POLICY.user,
                                       auth_source="mcp_http")

    def call(self, tool, args=None, *, context=None):
        result = mcp._handle_tool(tool, args or {}, security_context=context or self.context)
        self.assertFalse(result["isError"], result)
        return json.loads(result["content"][0]["text"])

    def seed_memory(self, content, *, principal="mcp-http", tenant="tenant-a"):
        return self.bot.memory.store(content, {"_botboy_security": {
            "principal_id": principal, "org_id": tenant,
        }})

    def test_memory_write_overrides_forged_security_metadata(self):
        forged = {"_botboy_security": {"principal_id": "victim", "org_id": "tenant-b"},
                  "label": "retained"}
        result = self.call("botboy_memory_store", {"content": "tenant-a new memory", "metadata": forged})
        stored = self.bot.memory.get(result["id"])
        self.assertEqual(stored.metadata["_botboy_security"],
                         {"principal_id": "mcp-http", "org_id": "tenant-a"})
        self.assertEqual(stored.metadata["label"], "retained")
        self.assertEqual(forged["_botboy_security"]["org_id"], "tenant-b")

    def test_memory_read_search_stats_and_pagination_keep_both_boundaries(self):
        # Create foreign rows first: filtering after LIMIT would hide the owned row.
        for index in range(4):
            self.seed_memory(f"shared search foreign-{index}", tenant="tenant-b")
            self.seed_memory(f"shared search other-{index}", principal="other-user")
        self.bot.memory.store("shared search legacy-unscoped")
        self.seed_memory("shared search owned-first")
        self.seed_memory("shared search owned-second")
        page = self.call("botboy_memory_list", {"limit": 1, "offset": 1})
        self.assertEqual(page["total"], 2)
        self.assertEqual(len(page["memories"]), 1)
        for item in page["memories"]:
            self.assertEqual(item["metadata"]["_botboy_security"],
                             {"principal_id": "mcp-http", "org_id": "tenant-a"})
        found = self.call("botboy_memory_search", {"query": "shared", "limit": 50})
        self.assertEqual(found["count"], 2)
        self.assertTrue(all("owned-" in row["content"] for row in found["results"]))
        self.assertEqual(self.call("botboy_memory_stats"), {"total": 2})
        tenant_b = self.call("botboy_memory_list", context=replace(self.context, org_id="tenant-b"))
        self.assertEqual(tenant_b["total"], 4)
        self.assertTrue(all("foreign-" in row["content"] for row in tenant_b["memories"]))

    def test_history_and_stats_restrict_same_principal_in_other_tenant(self):
        for principal, org, command in (("mcp-http", "tenant-a", "owned-command"),
                                        ("mcp-http", "tenant-b", "other-tenant-secret"),
                                        ("other-user", "tenant-a", "other-user-secret")):
            self.bot.history.record(command=command, output=command, principal=principal, org_id=org)
        result = self.call("botboy_history", {"limit": 100})
        self.assertEqual(result["total"], 1)
        self.assertEqual([row["command"] for row in result["records"]], ["owned-command"])
        stats = self.call("botboy_history_stats")
        self.assertEqual(stats["total"], 1)
        self.assertEqual(set(stats["by_principal"]), {"mcp-http"})
        result_b = self.call("botboy_history", context=replace(self.context, org_id="tenant-b"))
        self.assertEqual([row["command"] for row in result_b["records"]], ["other-tenant-secret"])

    def test_global_tools_and_generic_command_bypasses_are_denied(self):
        before_tasks = self.bot.task_store.list_tasks(limit=100)[1]
        for tool, args in (("botboy_schedule_add", {"name": "escape", "schedule": "in 1m"}),
                           ("botboy_schedule_list", {}), ("botboy_system_info", {}),
                           ("botboy_memory_delete", {"query": "all", "approval": True}),
                           ("botboy_archetype_match", {"query": "secret"})):
            with self.subTest(tool=tool):
                result = mcp._handle_tool(tool, args, security_context=self.context)
                self.assertTrue(result["isError"], result)
        for command in ("system info", "task list", "task show arbitrary-id", "task cancel arbitrary-id",
                        "history", "plan inspect all users", "think secret", "hsearch secret", "schedule list"):
            with self.subTest(command=command):
                result = self.call("botboy_execute", {"command": command, "approval": True})
                self.assertFalse(result["success"], result)
                self.assertEqual(result["type"], "authorization_error")
        self.assertEqual(self.bot.task_store.list_tasks(limit=100)[1], before_tasks)
        self.assertEqual(self.bot.scheduler.list_tasks(), [])

    def test_status_redacts_process_wide_counts_and_persists_core_identity(self):
        self.seed_memory("foreign memory marker", tenant="tenant-b")
        self.bot.history.record(command="private", principal="other-user", org_id="tenant-b")
        result = self.call("botboy_status")
        self.assertTrue(result["success"], result)
        for label in ("Cache hit rate:", "Memories stored:", "Channel messages:", "Archetypes routed:",
                      "Trace runs:", "Tasks tracked:", "Worker nodes:", "Reflection entries:", "Delegation workers:"):
            self.assertNotIn(label, result["output"])
        task_id = result["data"]["task"]["task_id"]
        task = self.bot.task_store.get_task(task_id)
        self.assertEqual((task.principal, task.org_id), ("mcp-http", "tenant-a"))
        persisted = self.bot.lifecycle_service.load_security_context(task_id)
        self.assertEqual((persisted.principal_id, persisted.org_id, persisted.auth_source),
                         ("mcp-http", "tenant-a", "mcp_http"))
        history, total = self.bot.history.list(task_id=task_id, principal="mcp-http", org_id="tenant-a")
        self.assertEqual(total, 1)
        self.assertEqual(history[0].command, "status")

    def test_pure_calculation_uses_core_pipeline_and_correct_tenant(self):
        result = self.call("botboy_calculate", {"expression": "7 * (11 + 2)"})
        self.assertTrue(result["success"], result)
        self.assertIn("91", result["output"])
        task_id = result["data"]["task"]["task_id"]
        self.assertEqual(self.bot.lifecycle_service.load_security_context(task_id).org_id, "tenant-a")

    def test_contextvars_are_restored_after_success_denial_and_exception(self):
        prior = (_current_principal.set("outer-principal"), _current_org.set("outer-tenant"),
                 _current_roles.set(("outer-role",)))
        try:
            for tool, args in (("botboy_memory_stats", {}), ("botboy_system_info", {}),
                               ("botboy_memory_store", {})):
                with self.subTest(tool=tool):
                    result = mcp._handle_tool(tool, args, security_context=self.context)
                    if tool == "botboy_memory_store":
                        self.assertTrue(result["isError"])
                        self.assertNotIn("Traceback", result["content"][0]["text"])
                    self.assertEqual((_current_principal.get(), _current_org.get(), _current_roles.get()),
                                     ("outer-principal", "outer-tenant", ("outer-role",)))
        finally:
            _current_principal.reset(prior[0])
            _current_org.reset(prior[1])
            _current_roles.reset(prior[2])


if __name__ == "__main__":
    unittest.main()
