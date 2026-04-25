from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from botboy.intelligence_route_support import handle_intelligence_route


class _PlannerRuntimeError:
    async def plan(self, _task: str):
        raise RuntimeError("planner offline")


class _PlannerTypeError:
    async def plan(self, _task: str):
        raise TypeError("planner signature mismatch")


class _LLM:
    async def chat(self, command: str):
        return SimpleNamespace(success=True, content=f"echo:{command}", backend="stub", model="stub")


class _Skills:
    def find_skill_for_command(self, command: str):
        return "demo-skill" if command.startswith("demo") else None

    def get_contract_for_skill(self, _skill: str) -> dict:
        return {"needs_approval": True}

    async def execute_builtin(self, *_args, **_kwargs):
        return None

    async def execute_custom(self, *_args, **_kwargs):
        return None


class _Bot:
    def __init__(self) -> None:
        self.planner = None
        self.parallel_thinker = None
        self.skills = None
        self.llm = None
        self.monitor = None
        self.trace_store = None
        self._hybrid_memory = False
        self.memory = None

    def _trace_async_label(self, *_args, **_kwargs):
        async def _runner(coro):
            return await coro

        return _runner

    async def _handle_archetypes(self, command: str) -> dict:
        return {"success": True, "output": command, "type": "archetypes"}


class IntelligenceRouteSupportTest(unittest.TestCase):
    def test_plan_runtime_error_returns_failure(self) -> None:
        bot = _Bot()
        bot.planner = _PlannerRuntimeError()
        result = asyncio.run(handle_intelligence_route(bot, "plan release build", first="plan"))
        self.assertFalse(result["success"])
        self.assertEqual(result["type"], "plan")
        self.assertIn("planner offline", result["output"])

    def test_plan_programmer_error_bubbles(self) -> None:
        bot = _Bot()
        bot.planner = _PlannerTypeError()
        with self.assertRaises(TypeError):
            asyncio.run(handle_intelligence_route(bot, "plan release build", first="plan"))

    def test_skill_policy_denied_returns_contract_payload(self) -> None:
        bot = _Bot()
        bot.skills = _Skills()
        denied_policy = SimpleNamespace(
            allowed=False,
            reason="approval required",
            to_dict=lambda: {"allowed": False, "reason": "approval required"},
        )
        with patch("botboy.skills.runtime.resolve_runtime_policy", return_value=denied_policy):
            result = asyncio.run(
                handle_intelligence_route(
                    bot,
                    "demo run",
                    first="demo",
                    principal="release.test",
                    approval_context={"granted": False},
                )
            )
        self.assertFalse(result["success"])
        self.assertEqual(result["type"], "skill")
        self.assertEqual(result["data"]["skill"], "demo-skill")

    def test_llm_fallback_returns_response(self) -> None:
        bot = _Bot()
        bot.llm = _LLM()
        result = asyncio.run(handle_intelligence_route(bot, "unknown question", first="unknown"))
        self.assertTrue(result["success"])
        self.assertEqual(result["type"], "llm")
        self.assertIn("echo:unknown question", result["output"])
