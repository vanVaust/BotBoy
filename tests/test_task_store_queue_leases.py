from __future__ import annotations

import unittest
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from botboy.tasks import TaskStore


class QueueLeaseContractTest(unittest.TestCase):
    def setUp(self) -> None:
        temp_root = (Path(__file__).resolve().parents[1] / ".botboy-runtime" / self._testMethodName).resolve()
        shutil.rmtree(temp_root, ignore_errors=True)
        temp_root.mkdir(parents=True, exist_ok=True)
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

    def _acquire(self, task_id: str = "task-lease-1"):
        return self.store.acquire_queue_lease(
            queue_name=self.node["queue_name"],
            node_id=self.node["node_id"],
            task_id=task_id,
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
        self.assertTrue(lease["is_active"])
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
        self.assertGreaterEqual(summary.get("active_lease_count", 0), 1)
        self.assertEqual(released["lease_status"], "released")
        self.assertFalse(released["is_active"])
        self.assertTrue(any(item["lease_id"] == lease["lease_id"] for item in released_leases))

    def test_max_parallelism_blocks_third_active_lease(self) -> None:
        self._acquire("task-lease-1")
        self._acquire("task-lease-2")

        with self.assertRaises(ValueError):
            self._acquire("task-lease-3")

        summary = self.store.queue_summary()
        self.assertEqual(summary["active_lease_count"], 2)
        self.assertEqual(summary["queue_depths"][self.node["queue_name"]], 2)

    def test_drain_prevents_new_lease_acquisition(self) -> None:
        self.store.drain_worker_node(self.node["node_id"], reason="maintenance")

        with self.assertRaises(ValueError):
            self._acquire()

    def test_acquire_rejects_finalized_task(self) -> None:
        task = self.store.create_task(
            title="finalized task",
            command="status",
            owner="worker:planner",
            delegated_to_worker="planner",
            status="completed",
        )

        with self.assertRaisesRegex(ValueError, "not leaseable"):
            self.store.acquire_queue_lease(
                queue_name=self.node["queue_name"],
                node_id=self.node["node_id"],
                task_id=task.task_id,
                principal="tester",
                request_id="req-finalized",
                lease_ttl_seconds=300,
            )

    def test_expired_lease_is_visible_and_recoverable(self) -> None:
        lease = self._acquire()
        expired_at = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
        conn = self.store._get_conn()
        conn.execute(
            "UPDATE queue_leases SET lease_expires_at = ? WHERE lease_id = ?",
            (expired_at, lease["lease_id"]),
        )
        conn.commit()

        active = self.store.list_queue_leases(queue_name=self.node["queue_name"])
        expired = self.store.list_queue_leases(queue_name=self.node["queue_name"], include_expired=True)
        summary = self.store.queue_summary()

        self.assertFalse(any(item["lease_id"] == lease["lease_id"] for item in active))
        self.assertTrue(any(item["lease_id"] == lease["lease_id"] and item["lease_status"] == "expired" for item in expired))
        self.assertEqual(summary["active_lease_count"], 0)
        self.assertGreaterEqual(summary["expired_lease_count"], 1)

    def test_claim_next_and_report_success(self) -> None:
        task = self.store.create_task(
            title="queued delegated task",
            command="status",
            owner="worker:planner",
            delegated_to_worker="planner",
            status="queued",
        )

        claim = self.store.claim_next_queue_lease(
            queue_name=self.node["queue_name"],
            node_id=self.node["node_id"],
            worker_id="planner",
            principal="tester",
            request_id="req-claim-1",
            lease_ttl_seconds=300,
        )
        self.assertIsNotNone(claim)
        assert claim is not None
        self.assertEqual(claim["task"]["task_id"], task.task_id)
        self.assertEqual(claim["task"]["status"], "running")

        report = self.store.report_queue_lease_result(
            claim["lease"]["lease_id"],
            success=True,
            result={"success": True, "output": "done"},
            summary="done",
            principal="tester",
            request_id="req-report-1",
            node_id=self.node["node_id"],
            worker_id="planner",
            idempotency_key="report-success-1",
        )
        replayed = self.store.report_queue_lease_result(
            claim["lease"]["lease_id"],
            success=True,
            result={"success": True, "output": "done"},
            summary="done",
            principal="tester",
            request_id="req-report-1-repeat",
            node_id=self.node["node_id"],
            worker_id="planner",
            idempotency_key="report-success-1",
        )
        refreshed = self.store.get_task(task.task_id)

        self.assertTrue(report["reported"])
        self.assertFalse(report["idempotent"])
        self.assertTrue(replayed["idempotent"])
        self.assertEqual(refreshed.status, "completed")
        self.assertEqual(report["lease"]["lease_status"], "released")

        with self.assertRaises(ValueError):
            self.store.report_queue_lease_result(
                claim["lease"]["lease_id"],
                success=True,
                node_id=self.node["node_id"],
                worker_id="planner",
                idempotency_key="different-report-key",
            )

    def test_claim_next_can_target_specific_task_id(self) -> None:
        older = self.store.create_task(
            title="older queued delegated task",
            command="older",
            owner="worker:planner",
            delegated_to_worker="planner",
            status="queued",
        )
        target = self.store.create_task(
            title="target queued delegated task",
            command="target",
            owner="worker:planner",
            delegated_to_worker="planner",
            status="queued",
        )

        claim = self.store.claim_next_queue_lease(
            queue_name=self.node["queue_name"],
            node_id=self.node["node_id"],
            worker_id="planner",
            task_id=target.task_id,
            principal="tester",
            request_id="req-claim-target",
            lease_ttl_seconds=300,
        )

        self.assertIsNotNone(claim)
        assert claim is not None
        self.assertEqual(claim["task"]["task_id"], target.task_id)
        self.assertEqual(self.store.get_task(target.task_id).status, "running")
        self.assertEqual(self.store.get_task(older.task_id).status, "queued")

    def test_report_rejects_payload_drift_for_same_idempotency_key(self) -> None:
        self.store.create_task(
            title="payload drift task",
            command="status",
            owner="worker:planner",
            delegated_to_worker="planner",
            status="queued",
        )
        claim = self.store.claim_next_queue_lease(
            queue_name=self.node["queue_name"],
            node_id=self.node["node_id"],
            worker_id="planner",
            principal="tester",
            request_id="req-claim-drift",
            lease_ttl_seconds=300,
        )
        self.assertIsNotNone(claim)
        assert claim is not None

        self.store.report_queue_lease_result(
            claim["lease"]["lease_id"],
            success=True,
            result={"success": True, "output": "done"},
            summary="done",
            principal="tester",
            request_id="req-report-drift-1",
            node_id=self.node["node_id"],
            worker_id="planner",
            idempotency_key="report-drift-1",
        )

        with self.assertRaisesRegex(ValueError, "different payload"):
            self.store.report_queue_lease_result(
                claim["lease"]["lease_id"],
                success=True,
                result={"success": True, "output": "changed"},
                summary="changed",
                principal="tester",
                request_id="req-report-drift-2",
                node_id=self.node["node_id"],
                worker_id="planner",
                idempotency_key="report-drift-1",
            )

    def test_report_rejects_wrong_node_or_worker_identity(self) -> None:
        task = self.store.create_task(
            title="identity-bound task",
            command="status",
            owner="worker:planner",
            delegated_to_worker="planner",
            status="queued",
        )
        claim = self.store.claim_next_queue_lease(
            queue_name=self.node["queue_name"],
            node_id=self.node["node_id"],
            worker_id="planner",
            principal="tester",
            request_id="req-claim-identity",
            lease_ttl_seconds=300,
        )
        self.assertIsNotNone(claim)
        assert claim is not None

        with self.assertRaisesRegex(ValueError, "not assigned to node"):
            self.store.report_queue_lease_result(
                claim["lease"]["lease_id"],
                success=True,
                node_id="other-node",
                worker_id="planner",
            )
        with self.assertRaisesRegex(ValueError, "not assigned to worker"):
            self.store.report_queue_lease_result(
                claim["lease"]["lease_id"],
                success=True,
                node_id=self.node["node_id"],
                worker_id="executor",
            )

        self.assertEqual(self.store.get_task(task.task_id).status, "running")

    def test_renew_and_report_reject_stale_fencing_token(self) -> None:
        self.store.create_task(
            title="fencing task",
            command="status",
            owner="worker:planner",
            delegated_to_worker="planner",
            status="queued",
        )
        claim = self.store.claim_next_queue_lease(
            queue_name=self.node["queue_name"],
            node_id=self.node["node_id"],
            worker_id="planner",
            principal="tester",
            request_id="req-claim-fencing",
            lease_ttl_seconds=300,
        )
        self.assertIsNotNone(claim)
        assert claim is not None
        fencing_token = str((claim["lease"].get("metadata") or {}).get("fencing_token", "") or "")

        with self.assertRaisesRegex(ValueError, "stale fencing token"):
            self.store.renew_queue_lease(
                claim["lease"]["lease_id"],
                principal="tester",
                request_id="req-renew-stale",
                lease_ttl_seconds=300,
                fencing_token="fence-stale",
            )

        with self.assertRaisesRegex(ValueError, "stale fencing token"):
            self.store.report_queue_lease_result(
                claim["lease"]["lease_id"],
                success=True,
                result={"success": True, "output": "done"},
                summary="done",
                principal="tester",
                request_id="req-report-stale",
                node_id=self.node["node_id"],
                worker_id="planner",
                fencing_token="fence-stale",
            )

        report = self.store.report_queue_lease_result(
            claim["lease"]["lease_id"],
            success=True,
            result={"success": True, "output": "done"},
            summary="done",
            principal="tester",
            request_id="req-report-valid",
            node_id=self.node["node_id"],
            worker_id="planner",
            fencing_token=fencing_token,
        )
        self.assertTrue(report["reported"])
        self.assertEqual(report["lease"]["lease_status"], "released")

    def test_release_rejects_wrong_binding_or_stale_fencing_token(self) -> None:
        self.store.create_task(
            title="release fencing task",
            command="status",
            owner="worker:planner",
            delegated_to_worker="planner",
            status="queued",
        )
        claim = self.store.claim_next_queue_lease(
            queue_name=self.node["queue_name"],
            node_id=self.node["node_id"],
            worker_id="planner",
            principal="tester",
            request_id="req-claim-release",
            lease_ttl_seconds=300,
        )
        self.assertIsNotNone(claim)
        assert claim is not None
        fencing_token = str((claim["lease"].get("metadata") or {}).get("fencing_token", "") or "")

        with self.assertRaisesRegex(ValueError, "not assigned to node"):
            self.store.release_queue_lease(
                claim["lease"]["lease_id"],
                principal="tester",
                request_id="req-release-node",
                node_id="other-node",
            )
        with self.assertRaisesRegex(ValueError, "stale fencing token"):
            self.store.release_queue_lease(
                claim["lease"]["lease_id"],
                principal="tester",
                request_id="req-release-fence",
                node_id=self.node["node_id"],
                worker_id="planner",
                fencing_token="fence-stale",
            )

        released = self.store.release_queue_lease(
            claim["lease"]["lease_id"],
            principal="tester",
            request_id="req-release-valid",
            node_id=self.node["node_id"],
            worker_id="planner",
            fencing_token=fencing_token,
        )
        self.assertEqual(released["lease_status"], "released")

    def test_claim_next_respects_retry_backoff_and_report_retry(self) -> None:
        task = self.store.create_task(
            title="backoff task",
            command="status",
            owner="worker:planner",
            delegated_to_worker="planner",
            status="queued",
            retry_after_s=3600,
        )
        claim = self.store.claim_next_queue_lease(
            queue_name=self.node["queue_name"],
            node_id=self.node["node_id"],
            worker_id="planner",
            principal="tester",
            request_id="req-claim-backoff",
        )
        self.assertIsNone(claim)

        self.store.update_task(task.task_id, retry_after_s=0)
        claim = self.store.claim_next_queue_lease(
            queue_name=self.node["queue_name"],
            node_id=self.node["node_id"],
            worker_id="planner",
            principal="tester",
            request_id="req-claim-2",
        )
        self.assertIsNotNone(claim)
        assert claim is not None
        report = self.store.report_queue_lease_result(
            claim["lease"]["lease_id"],
            success=False,
            transient=True,
            retry_after_s=7,
            error="temporary outage",
            principal="tester",
            request_id="req-report-2",
        )
        refreshed = self.store.get_task(task.task_id)

        self.assertTrue(report["retry_scheduled"])
        self.assertEqual(refreshed.status, "queued")
        self.assertEqual(refreshed.retry_after_s, 7)
        self.assertEqual(report["lease"]["lease_status"], "released")


if __name__ == "__main__":
    unittest.main()
