from __future__ import annotations

import unittest

from botboy.history_support import handle_history


class _FakeRecord:
    def __init__(self, command: str, *, success: bool = True, principal: str = "tester") -> None:
        self.success = success
        self.cmd_type = "task"
        self.command = command
        self.latency_ms = 12.5
        self.principal = principal
        self.request_id = "req-1"


class _FakeHistory:
    def __init__(self) -> None:
        self.records = [_FakeRecord("task list"), _FakeRecord("task show x", success=False)]

    def stats(self) -> dict:
        return {
            "total": 2,
            "success_count": 1,
            "success_rate": 0.5,
            "avg_latency_ms": 12.5,
            "by_type": {"task": 2},
            "by_principal": {"tester": 2},
        }

    def purge(self, older_than_days: int = 0) -> int:
        return 2

    def list(self, limit: int = 10, principal: str | None = None, request_id: str | None = None):
        records = self.records[:limit]
        if principal is not None:
            records = [record for record in records if record.principal == principal]
        if request_id is not None:
            records = [record for record in records if record.request_id == request_id]
        return records, len(records)


class _FakeBot:
    def __init__(self) -> None:
        self.history = _FakeHistory()


class HistorySupportTest(unittest.TestCase):
    def test_handle_history_stats(self) -> None:
        result = handle_history(_FakeBot(), "history stats")
        self.assertTrue(result["success"])
        self.assertIn("History stats:", result["output"])
        self.assertIn("task=2", result["output"])

    def test_handle_history_principal(self) -> None:
        result = handle_history(_FakeBot(), "history principal tester")
        self.assertTrue(result["success"])
        self.assertIn("principal 'tester'", result["output"])
        self.assertIn("request_id=req-1", result["output"])

    def test_handle_history_clear(self) -> None:
        result = handle_history(_FakeBot(), "history clear")
        self.assertTrue(result["success"])
        self.assertIn("Cleared 2 history records.", result["output"])


if __name__ == "__main__":
    unittest.main()
