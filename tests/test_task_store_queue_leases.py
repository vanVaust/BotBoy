from __future__ import annotations

import unittest
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from botboy.tasks import TaskStore


class QueueLeaseContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        required_methods = [
            "acquire_queue_lease",
            "renew_queue_lease",
            "release_queue_lease",
            "list_queue_leases",
            "queue_summary",
        ]
        missing = [name for name in required_methods if not hasattr(TaskStore, name)]
        if missing:
            raise unittest.SkipTest("Queue lease APIs not implemented yet: " + ", ".join(missing))

    def setUp(self) -> None:
        base_root = (Path(__file__).resolve().parents[1] / ".botboy-runtime" / "queue-lease-tests").resolve()
        base_root.mkdir(parents=True, exist_ok=True)
        temp_root = Path(tempfile.mkdtemp(prefix=f"{self._testMethodName}-", dir=base_root)).resolve()
        self.addCleanup(lambda: shutil.rmtree(temp_root, ignore_errors=True))
        self.db_path = str(temp_root / "tasks.db")
        self.artifact_root = str(temp_root / "artifacts" / "tasks")
        self.store = TaskStore(db_path=self.db_path, artifact_root=self.artifact_root)
        self.addCleanup(self.store.close)
        self.node = self.store.register_worker_node(
            node_id="node-queue-1",
            worker_id="planner",
            endpoint="http://127.0.0.1:9111",
            capabilities=["decomposition", "routing"],
            max_parallelism=2,
        )

    def _acquire(self):
        return self.store.acquire_queue_lease(
            queue_name=self.node["queue_name"],
            node_id=self.node["node_id"],
            task_id="task-lease-1",
            principal="tester",
            request_id="req-lease-1",
            lease_ttl_seconds=300,
        )

    def test_acquire_and_renew_queue_lease(self) -> None:
        lease = self._acquire()
        renewed = self.store.renew_queue_lease(
            lease["lease_id"],
            principal="tester",
            request_id="req-lease-2",
            lease_ttl_seconds=300,
        )

        self.assertEqual(lease["queue_name"], self.node["queue_name"])
        self.assertEqual(lease["node_id"], self.node["node_id"])
        self.assertEqual(lease["task_id"], "task-lease-1")
        self.assertEqual(lease["lease_status"], "active")
        self.assertEqual(renewed["lease_id"], lease["lease_id"])
        self.assertNotEqual(renewed["lease_expires_at"], lease["lease_expires_at"])

    def test_list_release_and_summary(self) -> None:
        lease = self._acquire()
        leases = self.store.list_queue_leases(queue_name=self.node["queue_name"])
        summary = self.store.queue_summary()
        released = self.store.release_queue_lease(
            lease["lease_id"],
            principal="tester",
            request_id="req-lease-3",
            reason="drain",
        )
        released_leases = self.store.list_queue_leases(queue_name=self.node["queue_name"], include_released=True)

        self.assertTrue(any(item["lease_id"] == lease["lease_id"] for item in leases))
        self.assertGreaterEqual(summary.get("queue_count", 0), 1)
        self.assertGreaterEqual(summary.get("lease_count", 0), 1)
        self.assertEqual(released["lease_status"], "released")
        self.assertFalse(
            any(
                item["lease_id"] == lease["lease_id"]
                for item in self.store.list_queue_leases(queue_name=self.node["queue_name"], include_released=False)
            )
        )
        self.assertTrue(any(item["lease_id"] == lease["lease_id"] for item in released_leases))

    def test_max_parallelism_and_expiry_recovery(self) -> None:
        first = self._acquire()
        second = self.store.acquire_queue_lease(
            queue_name=self.node["queue_name"],
            node_id=self.node["node_id"],
            task_id="task-lease-2",
            principal="tester",
            request_id="req-lease-2",
            lease_ttl_seconds=300,
        )
        blocked = self.store.acquire_queue_lease(
            queue_name=self.node["queue_name"],
            node_id=self.node["node_id"],
            task_id="task-lease-3",
            principal="tester",
            request_id="req-lease-3",
            lease_ttl_seconds=300,
        )

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertIsNone(blocked)
        self.assertEqual(self.store.queue_summary()["active_lease_count"], 2)

        expired_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        conn = self.store._get_conn()
        conn.execute(
            "UPDATE queue_leases SET lease_expires_at = ? WHERE lease_id = ?",
            (expired_at, first["lease_id"]),
        )
        conn.commit()

        recovered_slot = self.store.acquire_queue_lease(
            queue_name=self.node["queue_name"],
            node_id=self.node["node_id"],
            task_id="task-lease-3",
            principal="tester",
            request_id="req-lease-4",
            lease_ttl_seconds=300,
        )
        summary = self.store.queue_summary()
        expired = self.store.get_queue_lease(first["lease_id"])

        self.assertIsNotNone(recovered_slot)
        self.assertEqual(expired["lease_status"], "expired")
        self.assertEqual(summary["active_lease_count"], 2)
        self.assertGreaterEqual(summary["expired_lease_count"], 1)
        self.assertGreaterEqual(summary["recoverable_lease_count"], 1)

    def test_drain_prevents_new_queue_lease_assignment(self) -> None:
        self.store.drain_worker_node(self.node["node_id"], reason="maintenance")

        lease = self._acquire()

        self.assertIsNone(lease)
        self.assertEqual(self.store.queue_summary()["active_lease_count"], 0)


if __name__ == "__main__":
    unittest.main()
