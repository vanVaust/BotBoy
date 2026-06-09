from __future__ import annotations

import unittest

from botboy.gateway.simple_read_handlers import (
    handle_history_get,
    handle_history_stats,
    handle_scheduler_get,
)


class _FakeHistoryRecord:
    def __init__(self, record_id: str) -> None:
        self.record_id = record_id

    def to_dict(self) -> dict:
        return {"record_id": self.record_id}


class _FakeScheduledTask:
    def __init__(self, task_id: str) -> None:
        self.task_id = task_id

    def to_dict(self) -> dict:
        return {"task_id": self.task_id}


class _FakeHistory:
    def list(self, limit: int = 20, offset: int = 0, search=None, request_id=None):
        return [_FakeHistoryRecord("h-1")], 1

    def stats(self) -> dict:
        return {"total": 1}


class _FakeScheduler:
    def list_tasks(self):
        return [_FakeScheduledTask("s-1")]


class _FakeBot:
    def __init__(self) -> None:
        self.history = _FakeHistory()
        self.scheduler = _FakeScheduler()


class _FakeHandler:
    def __init__(self) -> None:
        self.botboy = _FakeBot()
        self.auth_enabled = False
        self.payloads: list[tuple[dict, int]] = []

    def _ensure_access(self, require_auth: bool = False):
        return "tester"

    def _json(self, payload: dict, status: int = 200) -> None:
        self.payloads.append((payload, status))


class SimpleReadHandlersTest(unittest.TestCase):
    def test_handle_history_get(self) -> None:
        handler = _FakeHandler()
        handle_history_get(handler, {"limit": ["5"], "offset": ["0"]})
        payload, status = handler.payloads[-1]
        self.assertEqual(status, 200)
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["records"][0]["record_id"], "h-1")

    def test_handle_history_stats(self) -> None:
        handler = _FakeHandler()
        handle_history_stats(handler, {})
        payload, status = handler.payloads[-1]
        self.assertEqual(status, 200)
        self.assertEqual(payload["total"], 1)

    def test_handle_scheduler_get(self) -> None:
        handler = _FakeHandler()
        handle_scheduler_get(handler, {})
        payload, status = handler.payloads[-1]
        self.assertEqual(status, 200)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["tasks"][0]["task_id"], "s-1")


if __name__ == "__main__":
    unittest.main()
