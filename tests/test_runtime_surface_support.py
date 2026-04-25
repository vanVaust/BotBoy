from __future__ import annotations

import unittest

from botboy.runtime_surface_support import build_help_response, build_status_response


class _FakeSkills:
    def list_skills(self) -> list[str]:
        return ["alpha", "beta"]


class _FakeCacheStats:
    hit_rate = 0.5
    hits = 2
    misses = 2


class _FakeCache:
    def stats(self):
        return _FakeCacheStats()


class _FakeMemory:
    def get_stats(self) -> dict:
        return {"total": 3}


class _FakeRouter:
    def stats(self) -> dict:
        return {"total_messages": 7}


class _FakeArchetypes:
    def stats(self) -> dict:
        return {"total_commands_routed": 5}


class _FakeTraceStore:
    def summary(self) -> dict:
        return {"total_runs": 2, "total_spans": 8}


class _FakeTaskStore:
    def summary(self) -> dict:
        return {"total": 4, "active_count": 1, "waiting_approval_count": 1}


class _FakeScheduler:
    _running = True


class _FakeBot:
    VERSION = "0.6.0.dev0"

    def __init__(self) -> None:
        self.memory = _FakeMemory()
        self.skills = _FakeSkills()
        self.cache = _FakeCache()
        self.scheduler = _FakeScheduler()
        self.history = object()
        self.trace_store = _FakeTraceStore()
        self.task_store = _FakeTaskStore()
        self.llm = None
        self._hybrid_memory = False
        self.router = _FakeRouter()
        self.archetypes = _FakeArchetypes()
        self.reflection_archive = object()
        self.delegation_advisor = object()
        self.a2a_pilot = object()

    def get_reflection_memory_payload(self) -> dict:
        return {"total_entries": 2}

    def get_delegation_intelligence_payload(self) -> dict:
        return {"worker_count": 4}

    def get_a2a_pilot_payload(self) -> dict:
        return {"total_adapters": 3}


class RuntimeSurfaceSupportTest(unittest.TestCase):
    def test_build_help_response(self) -> None:
        result = build_help_response(_FakeBot())
        self.assertTrue(result["success"])
        self.assertIn("Available Commands", result["output"])
        self.assertIn("task merge queue", result["output"])
        self.assertIn("security remote-readiness", result["output"])

    def test_build_status_response(self) -> None:
        result = build_status_response(_FakeBot())
        self.assertTrue(result["success"])
        self.assertIn("BotBoy v0.6.0.dev0 - Status", result["output"])
        self.assertIn("A2A adapters: 3", result["output"])


if __name__ == "__main__":
    unittest.main()
