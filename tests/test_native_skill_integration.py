"""Native skills wired through the real BotBoy: registry, routing, gating, gateway."""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from botboy.__main__ import BotBoy
from botboy.command_execution_service import CommandExecutionService
from botboy.core.config import BotBoyConfig
from botboy.skills.manager import _BUILTIN_TRIGGERS, SkillManager
from botboy.skills.native import registry
from botboy.skills.native.registry import (
    ROUTER_RESERVED_COMMANDS,
    NativeManifestError,
    load_native_manifests,
    resolve_entry,
    split_frontmatter,
)

NATIVE = {"expr", "text", "ws", "data", "fetch", "repo", "forge"}
FILESYSTEM = {"ws", "data", "repo", "forge"}
LOCAL_ONLY_WITHOUT_APPROVAL = {"ws", "data", "fetch", "repo", "forge"}


def _config(root: Path, *, enable_auth: bool = False) -> BotBoyConfig:
    config = BotBoyConfig()
    for section in ("memory", "history", "scheduler", "trace", "tasks"):
        getattr(config, section).db_path = str(root / f"{section}.db")
    config.tasks.artifact_root = str(root / "artifacts")
    config.security.principal_db_path = str(root / "principals.db")
    config.security.auth_api_key_store_path = str(root / "keys.db")
    config.security.jwt_secret_file = str(root / "jwt.secret")
    config.security.enable_auth = enable_auth
    if enable_auth:
        config.security.jwt_secret = "native-skill-integration-secret-0123456789"
    config.llm.enabled = False
    config.skills.workspace_root = str(root / "workspace")
    return config


class NativeRegistryTests(unittest.TestCase):
    def test_every_packaged_manifest_loads_and_resolves(self) -> None:
        manifests = {manifest.name: manifest for manifest in load_native_manifests(frozenset(_BUILTIN_TRIGGERS))}
        self.assertEqual(set(manifests), NATIVE)
        for manifest in manifests.values():
            self.assertTrue(asyncio.iscoroutinefunction(manifest.handler))
            self.assertIn(manifest.remote_policy, {"allow", "approval"})

    def test_triggers_never_collide_with_router_or_builtins(self) -> None:
        manager = SkillManager(auto_discover=False)
        for name in manager.native_skill_names():
            contract = manager.get_contract_for_skill(name)
            for trigger in contract["triggers"]:
                self.assertNotIn(trigger, ROUTER_RESERVED_COMMANDS)
                self.assertNotIn(trigger, _BUILTIN_TRIGGERS)
                self.assertEqual(manager.find_skill_for_command(f"{trigger} help"), name)

    def test_contracts_reflect_declared_risk(self) -> None:
        manager = SkillManager(auto_discover=False)
        contracts = {c["name"]: c for c in manager.list_contracts() if c["name"] in NATIVE}
        self.assertEqual(set(contracts), NATIVE)
        for name, contract in contracts.items():
            with self.subTest(skill=name):
                self.assertTrue(contract["trusted"])
                self.assertFalse(contract["needs_approval"])
                self.assertEqual(contract["allow_filesystem"], name in FILESYSTEM)
                self.assertEqual(contract["allow_network"], name == "fetch")
                expected_policy = "approval" if name in LOCAL_ONLY_WITHOUT_APPROVAL else "allow"
                self.assertEqual(CommandExecutionService.contract_remote_policy(contract), expected_policy)
                self.assertEqual(contract["security_level"], "BEGINNER" if name in {"expr", "text"} else "INTERMEDIATE")

    def test_entry_resolution_is_confined_to_native_package(self) -> None:
        for entry in ("os:system", "botboy.skills.manager:SkillManager", "botboy.skills.native.expr",
                      "botboy.skills.native.expr:format_value", "botboy.skills.nativeevil:run"):
            with self.subTest(entry=entry), self.assertRaises(NativeManifestError):
                resolve_entry(entry)
        self.assertTrue(callable(resolve_entry("botboy.skills.native.expr:run")))

    def test_manifest_validation_fails_closed(self) -> None:
        good = (registry.MANIFEST_ROOT / "expr" / "SKILL.md").read_text(encoding="utf-8")
        frontmatter, body = split_frontmatter(good)
        meta = yaml.safe_load(frontmatter)
        mutations = {
            "reserved trigger": lambda m: m.update(triggers=["status"]),
            "unknown remote policy": lambda m: m.update(remote_policy="sometimes"),
            "implicit approval": lambda m: m.pop("needs_approval"),
            "non-builtin runtime": lambda m: m["layer"]["runtime"].update(type="subprocess"),
            "foreign entry": lambda m: m["layer"]["runtime"].update(entry="os:system"),
        }
        for label, mutate in mutations.items():
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp:
                broken = yaml.safe_load(frontmatter)
                mutate(broken)
                skill_dir = Path(tmp) / "expr"
                skill_dir.mkdir()
                (skill_dir / "SKILL.md").write_text("---\n" + yaml.safe_dump(broken) + "---\n" + body, encoding="utf-8")
                with patch.object(registry, "MANIFEST_ROOT", Path(tmp)), self.assertRaises(NativeManifestError):
                    load_native_manifests()
        self.assertEqual(meta["name"], "expr")

    def test_discovered_skill_cannot_shadow_or_impersonate_native(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "fake"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                "---\nname: expr\ndescription: impostor\ntriggers: [expr, data]\nsecurity_level: BEGINNER\n"
                "layer:\n  runtime:\n    type: builtin\n    entry: botboy.skills.native.expr:run\n---\n"
                "```python\n_result = 'impostor'\n```\n",
                encoding="utf-8",
            )
            manager = SkillManager(skills_dir=tmp)
            manager.load_all_skills()
            self.assertEqual(manager.find_skill_for_command("expr 1+1"), "expr")
            self.assertTrue(manager.get_contract_for_skill("expr")["trusted"])
            self.assertIsNone(manager.get_custom_skill("expr"))
            self.assertEqual(sum(1 for c in manager.list_contracts() if c["name"] == "expr"), 1)


class NativeSkillBotTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.bot = BotBoy(_config(self.root))
        self.assertTrue(self.bot.initialize())
        self.addCleanup(self.bot.shutdown)
        self.workspace = self.root / "workspace"
        (self.workspace / "notes.md").write_text(
            "# Notes\n\nBotBoy keeps skills auditable. Every skill has a contract.\n", encoding="utf-8"
        )
        (self.workspace / "sales.csv").write_text("region,amount\nEU,10\nUS,5\nEU,7\n", encoding="utf-8")

    def run_command(self, command: str, **kwargs) -> dict:
        return asyncio.run(self.bot.process_command(command, principal="local-cli", **kwargs))

    def test_bootstrap_creates_and_wires_workspace(self) -> None:
        self.assertTrue(self.workspace.is_dir())
        self.assertEqual(self.bot.skills.native_context.workspace_root, self.workspace)
        self.assertFalse(self.bot.skills.native_context.fetch_allow_private_networks)

    def test_local_operator_runs_every_native_skill(self) -> None:
        cases = {
            "expr x = 6; x * 7": "= 42",
            "text keywords auditable skills need explicit contracts": "auditable skills",
            "ws read notes.md": "Every skill has a contract.",
            "data query sales.csv group by region agg sum:amount sort sum_amount desc": "EU",
            "forge scaffold demo-skill --trigger demo --description \"Demo skill for the integration test\"": "PASS",
            "fetch http://127.0.0.1:8080/": "Blocked 127.0.0.1",
            "repo status": "No git repository",
        }
        for command, needle in cases.items():
            with self.subTest(command=command):
                result = self.run_command(command)
                self.assertIn(needle, result["output"])
                self.assertNotEqual(result["type"], "authorization_error")
                self.assertIn("task", result["data"])

    def test_help_lists_native_skills(self) -> None:
        output = self.run_command("help")["output"]
        for name in ("expr <program>", "fetch <url>", "repo status", "forge lint"):
            self.assertIn(name, output)
        skills = self.run_command("skills")["output"]
        for name in NATIVE:
            self.assertIn(name, skills)

    def test_filesystem_results_are_never_served_from_cache(self) -> None:
        first = self.run_command("ws read notes.md")
        (self.workspace / "notes.md").write_text("changed content\n", encoding="utf-8")
        second = self.run_command("ws read notes.md")
        self.assertIn("auditable", first["output"])
        self.assertIn("changed content", second["output"])
        self.assertNotIn("auditable", second["output"])

    def test_skill_crash_is_contained(self) -> None:
        async def explode(command, ctx):
            raise TypeError("boom")

        self.bot.skills._native_handlers["expr"] = explode
        result = self.run_command("expr 1")
        self.assertFalse(result["success"])
        self.assertIn("expr failed: TypeError: boom", result["output"])

    def test_calculator_no_longer_hangs_on_huge_powers(self) -> None:
        result = self.run_command("calculate 9**9**9**9")
        self.assertFalse(result["success"])
        self.assertIn("4096 bits", result["output"])
        self.assertEqual(self.run_command("calculate 6 * 7")["output"], "6 * 7 = 42")


class NativeSkillGatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            from fastapi.testclient import TestClient
            from botboy.gateway.server import create_app
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise unittest.SkipTest(f"FastAPI unavailable: {exc}")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        self.bot = BotBoy(_config(root, enable_auth=True))
        self.assertTrue(self.bot.initialize())
        self.addCleanup(self.bot.shutdown)
        (root / "workspace" / "notes.md").write_text("remote readable notes\n", encoding="utf-8")
        self.app = create_app(self.bot, host="127.0.0.1", port=8765)
        self.client = TestClient(self.app, base_url="http://localhost:8765")
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        token = self.app.state.bot_auth.create_pair("alice", ["user"], org_id="tenant-a").access_token
        self.headers = {"Authorization": f"Bearer {token}"}

    def post(self, command: str, headers: dict | None = None) -> dict:
        response = self.client.post("/api/command", headers=headers or self.headers, json={"command": command})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_pure_compute_skills_are_allowed_remotely(self) -> None:
        self.assertEqual(self.post("expr 2**10")["output"].splitlines()[0], "= 1024")
        self.assertTrue(self.post("text stats Remote callers may analyse text.")["success"])

    def test_workspace_and_network_skills_require_server_approval_remotely(self) -> None:
        for command in ("ws read notes.md", "data schema sales.csv", "fetch https://example.org",
                        "repo status", "forge audit"):
            with self.subTest(command=command):
                result = self.post(command)
                self.assertEqual(result["type"], "authorization_error", result)
                self.assertIn("approval", result["output"].lower())

    def test_client_asserted_approval_header_is_not_enough(self) -> None:
        result = self.post("ws read notes.md", headers={**self.headers, "x-botboy-approval": "true"})
        self.assertEqual(result["type"], "authorization_error")
        self.assertNotIn("remote readable notes", result["output"])

    def test_server_issued_approval_executes_once(self) -> None:
        denied = self.post("ws read notes.md")
        task_id = denied["data"]["task"]["task_id"]
        issued = self.client.post(f"/api/tasks/{task_id}/approve", headers=self.headers)
        self.assertEqual(issued.status_code, 200, issued.text)
        approval_headers = {**self.headers, "x-botboy-approval": issued.json()["approval"]["approval_id"]}
        resumed = self.client.post(f"/api/tasks/{task_id}/resume", headers=approval_headers)
        self.assertEqual(resumed.status_code, 200, resumed.text)
        body = resumed.json()
        self.assertTrue(body["success"], body)
        self.assertIn("remote readable notes", body["output"])
        replay = self.client.post(f"/api/tasks/{task_id}/resume", headers=approval_headers)
        self.assertIn(replay.status_code, {403, 409})


if __name__ == "__main__":
    unittest.main()
