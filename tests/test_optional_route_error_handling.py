from __future__ import annotations

import asyncio
import sqlite3
import unittest
from pathlib import Path
from types import SimpleNamespace

from botboy.__main__ import BotBoy
from botboy.core.config import BotBoyConfig


ROOT = Path(__file__).resolve().parents[1]


class _PlannerRuntimeError:
    async def plan(self, _task: str):
        raise RuntimeError("planner offline")


class _PlannerTypeError:
    async def plan(self, _task: str):
        raise TypeError("planner signature mismatch")


class _ArchetypeTelemetry:
    def match_intent(self, _command: str):
        return SimpleNamespace(
            archetype=SimpleNamespace(value="analysis"),
            confidence=0.9,
            matched_via="test",
        )

    def stats(self):
        return {"total_commands_routed": 0, "archetypes": []}

    def record_outcome(self, **_kwargs):
        raise sqlite3.OperationalError("archetype db locked")


class OptionalRouteErrorHandlingTest(unittest.TestCase):
    def _make_bot(self) -> BotBoy:
        temp_root = (ROOT / ".botboy-runtime" / "optional-route-errors" / self._testMethodName).resolve()
        temp_root.mkdir(parents=True, exist_ok=True)
        config = BotBoyConfig()
        config.memory.db_path = str(temp_root / "botboy.db")
        config.history.db_path = str(temp_root / "history.db")
        config.scheduler.db_path = str(temp_root / "scheduler.db")
        config.trace.db_path = str(temp_root / "traces.db")
        config.tasks.db_path = str(temp_root / "tasks.db")
        config.tasks.artifact_root = str(temp_root / "artifacts" / "tasks")
        config.security.principal_db_path = str(temp_root / "principals.db")
        config.security.auth_api_key_store_path = str(temp_root / "api_keys.db")
        config.security.jwt_secret_file = str(temp_root / "jwt.secret")
        config.security.enable_auth = False
        config.llm.enabled = False
        bot = BotBoy(config)
        self.assertTrue(bot.initialize())
        self.addCleanup(bot.shutdown)
        return bot

    def test_plan_runtime_error_returns_plan_failure(self) -> None:
        bot = self._make_bot()
        bot.planner = _PlannerRuntimeError()

        result = asyncio.run(bot._route("plan release build", principal="release.test"))

        self.assertFalse(result["success"])
        self.assertEqual(result["type"], "plan")
        self.assertIn("planner offline", result["output"])

    def test_plan_programmer_error_bubbles(self) -> None:
        bot = self._make_bot()
        bot.planner = _PlannerTypeError()

        with self.assertRaises(TypeError):
            asyncio.run(bot._route("plan release build", principal="release.test"))

    def test_load_task_merge_result_ignores_invalid_artifact_json(self) -> None:
        bot = self._make_bot()
        task = bot.task_store.create_task(
            title="merge root",
            principal="release.test",
            request_id="req-merge",
            command="status",
            status="queued",
        )
        artifact_path = (ROOT / ".botboy-runtime" / "optional-route-errors" / self._testMethodName / "broken-merge.json").resolve()
        artifact_path.write_text("{not-json", encoding="utf-8")
        bot.task_store.add_artifact_file(
            task.task_id,
            category="merge_report",
            label="broken merge",
            file_path=str(artifact_path),
            media_type="application/json",
            allow_external_source=True,
        )

        loaded = bot._load_task_merge_result(bot.task_store.get_task(task.task_id))

        self.assertEqual(loaded, {})

    def test_history_store_error_does_not_fail_command(self) -> None:
        bot = self._make_bot()

        def _raise_history_error(**_kwargs):
            raise sqlite3.OperationalError("history db locked")

        bot.history = SimpleNamespace(record=_raise_history_error, close=lambda: None)

        result = asyncio.run(bot.process_command("status", principal="release.test"))

        self.assertTrue(result["success"])
        self.assertEqual(result["type"], "status")

    def test_archetype_store_error_does_not_fail_command(self) -> None:
        bot = self._make_bot()
        bot.archetypes = _ArchetypeTelemetry()

        result = asyncio.run(bot.process_command("status", principal="release.test"))

        self.assertTrue(result["success"])
        self.assertEqual(result["type"], "status")
