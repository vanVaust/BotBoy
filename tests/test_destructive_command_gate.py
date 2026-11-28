from __future__ import annotations

import asyncio
import shutil
import tempfile
import unittest
import warnings
from pathlib import Path

from botboy.command_effects import (
    MEMORY_DELETE_COMMANDS,
    SCHEDULE_REMOVE_SUBCOMMANDS,
    destructive_effect,
)
from botboy.command_execution_service import CommandExecutionService
from botboy.planning import PLANNER_APPROVAL_CONTEXT, ExecutionPlan, PlanStep, PlanningEngine


class _Skills:
    @staticmethod
    def get_contract_for_command(_command):
        return None


class _Bot:
    def __init__(self, skills=True):
        self.skills = _Skills() if skills else None


class DestructiveCommandClassificationTests(unittest.TestCase):
    def test_every_destructive_alias_is_classified(self):
        classify = CommandExecutionService.is_destructive_command
        cases = {
            "history clear": "history.clear",
            " HISTORY   CLEAR ": "history.clear",
            "forget": "memory.delete",
            "Forget everything": "memory.delete",
            "delete zebra": "memory.delete",
            "DELETE\tzebra": "memory.delete",
            "schedule cancel abc123": "schedule.remove",
            "schedule remove abc123": "schedule.remove",
            "Schedule DELETE abc123": "schedule.remove",
        }
        for command, effect in cases.items():
            with self.subTest(command=command):
                self.assertTrue(classify(command))
                self.assertEqual(destructive_effect(command), effect)
        for command in ("history", "history stats", "history clearance", "forgetful", "remember forget",
                        "deleted", "schedule list", "schedule add x 5m", "search delete", ""):
            with self.subTest(command=command):
                self.assertFalse(classify(command), command)

    def test_handlers_and_router_use_the_shared_alias_sets(self):
        import inspect

        from botboy import command_router, history_support, runtime_command_support

        self.assertEqual(MEMORY_DELETE_COMMANDS, frozenset({"forget", "delete"}))
        self.assertEqual(SCHEDULE_REMOVE_SUBCOMMANDS, frozenset({"cancel", "remove", "delete"}))
        for module, name in (
            (runtime_command_support, "MEMORY_DELETE_COMMANDS"),
            (runtime_command_support, "SCHEDULE_REMOVE_SUBCOMMANDS"),
            (history_support, "HISTORY_DESTRUCTIVE_SUBCOMMANDS"),
            (command_router, "MEMORY_DELETE_COMMANDS"),
        ):
            self.assertIn(name, inspect.getsource(module), (module.__name__, name))
        # Literal alias tuples next to the dispatch would bypass the gate again.
        self.assertNotIn('("forget", "delete")', inspect.getsource(runtime_command_support))


class DestructiveCommandGateTests(unittest.TestCase):
    def test_gateway_worker_and_planner_contexts_need_server_approval(self):
        for with_skills in (False, True):
            service = CommandExecutionService(_Bot(skills=with_skills))
            contexts = [
                {"granted": True, "explicit": True, "source": source, "reason": "payload"}
                for source in ("fastapi", "stdlib", "worker_handoff")
            ] + [dict(PLANNER_APPROVAL_CONTEXT)]
            for context in contexts:
                for command in ("history clear", "forget everything", "delete zebra", "schedule cancel x"):
                    self.assertTrue(service._approval_required(command, context), (command, context))
            self.assertTrue(service._approval_required("history clear"))

    def test_local_cli_default_remains_available(self):
        service = CommandExecutionService(_Bot())
        self.assertFalse(service._approval_required("history clear", service._default_approval_context()))
        self.assertFalse(service._approval_required("delete zebra", service._default_approval_context()))
        spoofed = {"source": "local", "reason": "payload", "granted": True}
        self.assertTrue(service._approval_required("history clear", spoofed))
        self.assertFalse(service._approval_required("status", {"source": "fastapi"}))


class _RecordingBot:
    def __init__(self):
        self.calls = []

    async def process_command(self, command, **kwargs):
        self.calls.append((command, kwargs))
        return {"success": True, "output": "ok"}


class PlannerProvenanceTests(unittest.TestCase):
    def test_plan_steps_never_run_with_operator_default_approval(self):
        bot = _RecordingBot()
        plan = ExecutionPlan(
            task="t", strategy="s", executable=True, confidence=1.0, auditor_notes="", rounds=1,
            steps=[PlanStep(step_id="s1", description="", skill="memory", command="delete zebra",
                            risk_level="low", depends_on=[])],
        )
        planner = PlanningEngine.__new__(PlanningEngine)
        asyncio.run(planner.execute(plan, bot=bot))
        (command, kwargs), = bot.calls
        self.assertEqual(command, "delete zebra")
        self.assertEqual(kwargs["approval_context"]["source"], "planner")
        self.assertFalse(kwargs["approval_context"]["granted"])
        self.assertIsNot(kwargs["approval_context"], PLANNER_APPROVAL_CONTEXT)


class GatewayDestructiveAliasEndToEndTests(unittest.TestCase):
    """Regression: `delete <q>` deleted memories through the auth-disabled gateway."""

    def test_no_destructive_alias_succeeds_through_gateway(self):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            raise unittest.SkipTest("FastAPI test client unavailable")
        from botboy.__main__ import BotBoy
        from botboy.core.config import BotBoyConfig
        from botboy.gateway.server import create_app

        root = Path(tempfile.mkdtemp(prefix="destructive-gate-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        config = BotBoyConfig()
        config.memory.db_path = str(root / "botboy.db")
        config.history.db_path = str(root / "history.db")
        config.scheduler.db_path = str(root / "scheduler.db")
        config.trace.db_path = str(root / "traces.db")
        config.tasks.db_path = str(root / "tasks.db")
        config.tasks.artifact_root = str(root / "artifacts")
        config.security.principal_db_path = str(root / "principals.db")
        config.security.auth_api_key_store_path = str(root / "api_keys.db")
        config.security.jwt_secret_file = str(root / "jwt.secret")
        config.security.enable_auth = False
        config.llm.enabled = False
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            bot = BotBoy(config)
            self.assertTrue(bot.initialize())
        self.addCleanup(bot.shutdown)

        def local(command):
            return asyncio.run(bot.process_command(command, principal="local-cli"))

        for idx in range(3):
            self.assertTrue(local(f"remember zebra fact {idx}")["success"])
        with TestClient(create_app(bot, host="127.0.0.1", port=8765), base_url="http://127.0.0.1:8765") as client:
            for command in ("forget zebra", "delete zebra", "DELETE   zebra", "history clear"):
                with self.subTest(command=command):
                    body = client.post("/api/command", json={"command": command}).json()
                    self.assertEqual(body.get("type"), "authorization_error", body)
        self.assertIn("Found 3", local("search zebra")["output"])
        self.assertIn("Deleted 3", local("delete zebra")["output"])


if __name__ == "__main__":
    unittest.main()
