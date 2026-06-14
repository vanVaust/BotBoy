from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from botboy.core.self_healing import SelfHealingEngine
from botboy.tasks import TaskStore


class SelfHealingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = TaskStore(db_path=":memory:", artifact_root="")
        self.addCleanup(self.store.close)

    def test_self_healing_scan_for_stale_tasks(self) -> None:
        engine = SelfHealingEngine(self.store)
        task1 = self.store.create_task(title="Task 1", command="cmd1", principal="admin")
        self.store.update_status(task_id=task1.task_id, status="running", principal="admin")

        self.assertEqual(len(engine.scan_for_stale_tasks(timeout_seconds=60)), 0)

        old_time = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
        conn = self.store._get_conn()
        conn.execute("UPDATE tasks SET updated_at = ? WHERE task_id = ?", (old_time, task1.task_id))
        conn.commit()

        stale = engine.scan_for_stale_tasks(timeout_seconds=60)
        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0]["task_id"], task1.task_id)

    def test_self_healing_execute_auto_retry(self) -> None:
        engine = SelfHealingEngine(self.store)
        task1 = self.store.create_task(title="Task 1", command="cmd1", principal="admin", payload={"foo": "bar"})
        self.store.update_task(task_id=task1.task_id, status="running", delegated_to_worker="w-1")

        self.assertTrue(engine.execute_auto_retry(task1.task_id, max_retries=3))
        task = self.store.get_task(task1.task_id)
        self.assertEqual(task.status, "queued")
        self.assertEqual(task.delegated_to_worker, "")
        self.assertEqual(task.payload.get("_retry_count"), 1)

        for expected_count in (2, 3):
            self.store.update_task(task_id=task1.task_id, status="running", delegated_to_worker="w-1")
            self.assertTrue(engine.execute_auto_retry(task1.task_id, max_retries=3))
            self.assertEqual(self.store.get_task(task1.task_id).payload.get("_retry_count"), expected_count)

        self.store.update_task(task_id=task1.task_id, status="running", delegated_to_worker="w-1")
        self.assertFalse(engine.execute_auto_retry(task1.task_id, max_retries=3))
        self.assertEqual(self.store.get_task(task1.task_id).status, "failed")

    def test_self_healing_discover_alternative_capabilities(self) -> None:
        engine = SelfHealingEngine(None)

        discovered = engine.discover_alternative_capabilities(
            ["web_search", "python_execute", "unknown_capability"]
        )

        self.assertIn("duckduckgo_search", discovered)
        self.assertIn("sandbox_python", discovered)
        self.assertIn("unknown_capability", discovered)


if __name__ == "__main__":
    unittest.main()
