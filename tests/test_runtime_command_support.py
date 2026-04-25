from __future__ import annotations

import unittest

from botboy.runtime_command_support import handle_memory_command, handle_schedule


class _FakeMemoryRecord:
    def __init__(self, record_id: str, content: str) -> None:
        self.id = record_id
        self.content = content

    def to_dict(self) -> dict:
        return {"id": self.id, "content": self.content}


class _FakeMemory:
    def __init__(self) -> None:
        self.records = [_FakeMemoryRecord("m1", "hello world")]
        self.deleted: list[str] = []

    def store(self, text: str) -> str:
        self.records.append(_FakeMemoryRecord("m2", text))
        return "m2"

    def search(self, query: str, limit: int = 5):
        return [record for record in self.records if query in record.content][:limit]

    def delete(self, record_id: str) -> None:
        self.deleted.append(record_id)

    def list_all(self, limit: int = 20):
        return self.records[:limit]

    def get_stats(self) -> dict:
        return {"total": len(self.records), "oldest": "x", "newest": "y", "db_size_bytes": 2048}


class _FakeMetrics:
    def __init__(self) -> None:
        self.memory_ops = 0

    def record_memory_op(self) -> None:
        self.memory_ops += 1


class _FakeScheduledTask:
    def __init__(self, task_id: str, *, next_run_ts: int = 0) -> None:
        self.task_id = task_id
        self.name = "daily"
        self.schedule = "0 8 * * *"
        self.next_run_ts = next_run_ts


class _FakeScheduler:
    def __init__(self) -> None:
        self.cancelled: list[str] = []

    def list_tasks(self):
        return [_FakeScheduledTask("task-1", next_run_ts=0)]

    def add(self, *, name: str, schedule: str) -> str:
        return "task-new"

    def cancel(self, task_id: str) -> None:
        self.cancelled.append(task_id)

    def stats(self) -> dict:
        return {"active": 1, "total_runs": 3}


class _FakeBot:
    def __init__(self) -> None:
        self.memory = _FakeMemory()
        self.metrics = _FakeMetrics()
        self.scheduler = _FakeScheduler()


class RuntimeCommandSupportTest(unittest.TestCase):
    def test_handle_memory_command_remember(self) -> None:
        bot = _FakeBot()
        result = handle_memory_command(bot, "remember ship this", first="remember")
        self.assertTrue(result["success"])
        self.assertIn("Remembered", result["output"])
        self.assertEqual(bot.metrics.memory_ops, 1)

    def test_handle_memory_command_search(self) -> None:
        result = handle_memory_command(_FakeBot(), "search hello", first="search")
        self.assertTrue(result["success"])
        self.assertIn("Found 1 result", result["output"])

    def test_handle_memory_command_memstats(self) -> None:
        result = handle_memory_command(_FakeBot(), "memstats", first="memstats")
        self.assertTrue(result["success"])
        self.assertIn("Memory DB stats", result["output"])

    def test_handle_schedule_list(self) -> None:
        result = handle_schedule(_FakeBot(), "schedule list")
        self.assertTrue(result["success"])
        self.assertIn("Scheduled tasks", result["output"])

    def test_handle_schedule_cancel(self) -> None:
        bot = _FakeBot()
        result = handle_schedule(bot, "schedule cancel task-1")
        self.assertTrue(result["success"])
        self.assertIn("Cancelled task", result["output"])
        self.assertEqual(bot.scheduler.cancelled, ["task-1"])


if __name__ == "__main__":
    unittest.main()
