from __future__ import annotations

import unittest

from botboy.gateway.simple_runtime_read_handlers import (
    handle_dashboard_json,
    handle_health,
    handle_memories_get,
    handle_memories_search,
    handle_metrics_json,
    handle_metrics_text,
    handle_monitoring_json,
    handle_skills,
    handle_status,
)


class _FakeMemoryRecord:
    def __init__(self, record_id: str, content: str) -> None:
        self.id = record_id
        self.content = content

    def to_dict(self) -> dict:
        return {"id": self.id, "content": self.content}


class _FakeMemory:
    def list_all(self, limit: int = 20, offset: int = 0):
        return [_FakeMemoryRecord("m1", "alpha")]

    def get_stats(self) -> dict:
        return {"total": 1}

    def search(self, query: str, limit: int = 10):
        return [_FakeMemoryRecord("m1", query)]


class _FakeSkills:
    def list_skills(self) -> list[str]:
        return ["alpha", "beta"]


class _FakeCacheStats:
    hit_rate = 0.5
    size = 2


class _FakeCache:
    def stats(self):
        return _FakeCacheStats()


class _FakeMetrics:
    def render(self):
        return "metric 1\n", "text/plain"

    def to_json(self):
        return {"metric": 1}


class _FakeBot:
    VERSION = "0.6.0.dev0"

    def __init__(self) -> None:
        self.memory = _FakeMemory()
        self.skills = _FakeSkills()
        self.cache = _FakeCache()
        self.scheduler = object()
        self.history = object()
        self.trace_store = object()
        self.llm = None
        self.metrics = _FakeMetrics()

    async def process_command(self, command: str, *, principal: str, request_id: str, roles: list[str], approval_context: dict):
        return {"success": True, "output": "BotBoy v0.6.0.dev0 - Status", "type": "status"}

    def get_monitoring_payload(self) -> dict:
        return {"ok": True}


class _FakeHandler:
    def __init__(self) -> None:
        self.botboy = _FakeBot()
        self.auth_enabled = False
        self.rate_limiter = object()
        self.security = type("Security", (), {"auth_api_keys_enabled": True})()
        self.request_id = "req-1"
        self.json_payloads: list[tuple[dict, int]] = []
        self.text_payloads: list[tuple[str, str]] = []

    def _json(self, payload: dict, status: int = 200) -> None:
        self.json_payloads.append((payload, status))

    def _text(self, body: str, content_type: str = "text/plain") -> None:
        self.text_payloads.append((body, content_type))

    def _ensure_access(self, require_auth: bool = False):
        return "tester"

    def _ensure_access_with_roles(self, require_auth: bool = False):
        return "tester", ["operator"]

    def _approval_context(self, payload=None, roles=None) -> dict:
        return {"granted": True}

    def _task_store(self, optional: bool = False):
        return {"store": True} if optional else {"store": True}

    def _task_metrics(self, store) -> dict:
        return {"blocked_count": 0}

    def _task_workers_payload(self, store) -> dict:
        return {"workers": []}

    def _dashboard_payload(self, mode: str) -> dict:
        return {"mode": mode}


class SimpleRuntimeReadHandlersTest(unittest.TestCase):
    def test_handle_health(self) -> None:
        handler = _FakeHandler()
        handle_health(handler, {})
        payload, status = handler.json_payloads[-1]
        self.assertEqual(status, 200)
        self.assertEqual(payload["mode"], "stdlib")
        self.assertTrue(payload["components"]["memory"])

    def test_handle_status(self) -> None:
        handler = _FakeHandler()
        handle_status(handler, {})
        payload, status = handler.json_payloads[-1]
        self.assertEqual(status, 200)
        self.assertTrue(payload["success"])

    def test_handle_memories_and_skills(self) -> None:
        handler = _FakeHandler()
        handle_skills(handler, {})
        skills_payload, _ = handler.json_payloads[-1]
        handle_memories_get(handler, {"limit": ["5"], "offset": ["0"]})
        memories_payload, _ = handler.json_payloads[-1]
        handle_memories_search(handler, {"q": ["alpha"], "limit": ["5"]})
        search_payload, _ = handler.json_payloads[-1]
        self.assertEqual(skills_payload["count"], 2)
        self.assertEqual(memories_payload["total"], 1)
        self.assertEqual(search_payload["count"], 1)

    def test_handle_metrics_monitoring_dashboard(self) -> None:
        handler = _FakeHandler()
        handle_metrics_text(handler, {})
        handle_metrics_json(handler, {})
        metrics_payload, _ = handler.json_payloads[-1]
        handle_monitoring_json(handler, {})
        monitoring_payload, _ = handler.json_payloads[-1]
        handle_dashboard_json(handler, {})
        dashboard_payload, _ = handler.json_payloads[-1]
        self.assertEqual(handler.text_payloads[-1][0], "metric 1\n")
        self.assertEqual(metrics_payload["metric"], 1)
        self.assertTrue(monitoring_payload["ok"])
        self.assertEqual(dashboard_payload["mode"], "stdlib")


if __name__ == "__main__":
    unittest.main()
