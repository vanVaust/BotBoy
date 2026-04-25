from __future__ import annotations

import unittest
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from botboy.tasks import TaskStore
from botboy.worker_client import TaskStoreWorkerClient
from botboy.worker_daemon import WorkerDaemon, WorkerDaemonConfig, WorkerRetryableError, build_parser


class WorkerDaemonTest(unittest.TestCase):
    def _make_store(self) -> TaskStore:
        temp_root = (Path(__file__).resolve().parents[1] / ".botboy-runtime" / self._testMethodName).resolve()
        shutil.rmtree(temp_root, ignore_errors=True)
        temp_root.mkdir(parents=True, exist_ok=True)
        store = TaskStore(
            db_path=str(temp_root / "tasks.db"),
            artifact_root=str(temp_root / "artifacts" / "tasks"),
        )
        self.addCleanup(store.close)
        return store

    def _make_client(self, store: TaskStore) -> TaskStoreWorkerClient:
        return TaskStoreWorkerClient(
            store,
            worker_id="executor",
            node_id="node-daemon-1",
            queue_name="executor.node-daemon-1",
            lease_ttl_seconds=60,
        )

    def test_run_once_completes_task_and_releases_lease(self) -> None:
        store = self._make_store()
        client = self._make_client(store)
        task = store.create_task(
            title="daemon task",
            command="echo success",
            owner="worker:executor",
            delegated_to_worker="executor",
            status="queued",
        )

        async def _executor(record):
            return {
                "success": True,
                "output": f"processed:{record.command}",
                "type": "command",
                "data": {"task_id": record.task_id},
            }

        daemon = WorkerDaemon(
            client,
            executor=_executor,
            config=WorkerDaemonConfig(
                worker_id="executor",
                node_id="node-daemon-1",
                queue_name="executor.node-daemon-1",
                lease_ttl_seconds=60,
                once=True,
                heartbeat_interval_s=999,
                max_attempts=3,
            ),
            sleeper=lambda _seconds: None,
        )

        result = daemon.run_once()
        refreshed = store.get_task(task.task_id)
        leases = store.list_queue_leases(node_id="node-daemon-1", include_released=True, include_expired=True)

        self.assertEqual(result.processed, 1)
        self.assertEqual(refreshed.status, "completed")
        self.assertEqual(refreshed.attempt_count, 1)
        self.assertTrue(any(lease["lease_status"] == "released" for lease in leases))

    def test_run_once_notifies_on_task_finalized(self) -> None:
        store = self._make_store()
        client = self._make_client(store)
        task = store.create_task(
            title="callback task",
            command="echo callback",
            owner="worker:executor",
            delegated_to_worker="executor",
            status="queued",
        )
        finalized: list[tuple[str, str]] = []

        async def _executor(record):
            return {
                "success": True,
                "output": f"processed:{record.command}",
                "data": {"task_id": record.task_id},
            }

        daemon = WorkerDaemon(
            client,
            executor=_executor,
            config=WorkerDaemonConfig(
                worker_id="executor",
                node_id="node-daemon-1",
                queue_name="executor.node-daemon-1",
                lease_ttl_seconds=60,
                once=True,
                heartbeat_interval_s=999,
            ),
            sleeper=lambda _seconds: None,
            on_task_finalized=lambda record: finalized.append((record.task_id, record.status)),
        )

        result = daemon.run_once()

        self.assertEqual(result.processed, 1)
        self.assertEqual(finalized, [(task.task_id, "completed")])

    def test_run_once_with_target_task_id_skips_older_queued_task(self) -> None:
        store = self._make_store()
        client = self._make_client(store)
        older = store.create_task(
            title="older daemon task",
            command="echo older",
            owner="worker:executor",
            delegated_to_worker="executor",
            status="queued",
        )
        target = store.create_task(
            title="target daemon task",
            command="echo target",
            owner="worker:executor",
            delegated_to_worker="executor",
            status="queued",
        )
        executed: list[str] = []

        async def _executor(record):
            executed.append(record.task_id)
            return {
                "success": True,
                "output": f"processed:{record.command}",
                "data": {"task_id": record.task_id},
            }

        daemon = WorkerDaemon(
            client,
            executor=_executor,
            config=WorkerDaemonConfig(
                worker_id="executor",
                node_id="node-daemon-1",
                queue_name="executor.node-daemon-1",
                target_task_id=target.task_id,
                lease_ttl_seconds=60,
                once=True,
                heartbeat_interval_s=999,
            ),
            sleeper=lambda _seconds: None,
        )

        result = daemon.run_once()

        self.assertEqual(result.processed, 1)
        self.assertEqual(executed, [target.task_id])
        self.assertEqual(store.get_task(target.task_id).status, "completed")
        self.assertEqual(store.get_task(older.task_id).status, "queued")

    def test_run_once_schedules_retry_on_retryable_failure(self) -> None:
        store = self._make_store()
        client = self._make_client(store)
        task = store.create_task(
            title="retry task",
            command="echo retry",
            owner="worker:executor",
            delegated_to_worker="executor",
            status="queued",
        )

        def _executor(_record):
            raise WorkerRetryableError("temporary outage")

        daemon = WorkerDaemon(
            client,
            executor=_executor,
            config=WorkerDaemonConfig(
                worker_id="executor",
                node_id="node-daemon-1",
                queue_name="executor.node-daemon-1",
                lease_ttl_seconds=60,
                once=True,
                heartbeat_interval_s=999,
                retry_backoff_base_s=2.0,
                retry_backoff_max_s=30.0,
            ),
            sleeper=lambda _seconds: None,
        )

        result = daemon.run_once()
        refreshed = store.get_task(task.task_id)
        leases = store.list_queue_leases(node_id="node-daemon-1", include_released=True, include_expired=True)

        self.assertEqual(result.retried, 1)
        self.assertEqual(refreshed.status, "queued")
        self.assertGreaterEqual(refreshed.retry_after_s, 2)
        self.assertTrue(any(lease["lease_status"] == "released" for lease in leases))

    def test_recover_expired_lease_requeues_task(self) -> None:
        store = self._make_store()
        client = self._make_client(store)
        client.bootstrap_node(display_name="Executor Node")
        task = store.create_task(
            title="stale task",
            command="echo stale",
            owner="worker:executor",
            delegated_to_worker="executor",
            status="running",
            attempt_count=1,
            lease_expires_at=(datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(),
            heartbeat_at=(datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(),
        )
        lease = store.acquire_queue_lease(
            queue_name="executor.node-daemon-1",
            node_id="node-daemon-1",
            task_id=task.task_id,
            principal="tester",
            request_id="req-1",
            lease_ttl_seconds=60,
        )
        conn = store._get_conn()
        conn.execute(
            "UPDATE queue_leases SET lease_expires_at = ? WHERE lease_id = ?",
            ((datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(), lease["lease_id"]),
        )
        conn.commit()

        recovered = client.recover_expired_leases(
            principal="tester",
            request_id="req-recover",
            retry_backoff_s=4,
        )
        refreshed = store.get_task(task.task_id)
        leases = store.list_queue_leases(node_id="node-daemon-1", include_released=True, include_expired=True)

        self.assertEqual(recovered, [task.task_id])
        self.assertEqual(refreshed.status, "queued")
        self.assertEqual(refreshed.retry_after_s, 4)
        self.assertTrue(any(lease["lease_status"] == "released" for lease in leases))

    def test_recover_expired_leases_does_not_touch_active_running_lease(self) -> None:
        store = self._make_store()
        client = self._make_client(store)
        client.bootstrap_node(display_name="Executor Node")
        task = store.create_task(
            title="active task",
            command="echo active",
            owner="worker:executor",
            delegated_to_worker="executor",
            status="running",
            attempt_count=1,
        )
        lease = store.acquire_queue_lease(
            queue_name="executor.node-daemon-1",
            node_id="node-daemon-1",
            task_id=task.task_id,
            principal="tester",
            request_id="req-active",
            lease_ttl_seconds=60,
        )

        recovered = client.recover_expired_leases(
            principal="tester",
            request_id="req-recover-active",
            retry_backoff_s=4,
        )
        refreshed = store.get_task(task.task_id)
        active_lease = store.get_queue_lease(lease["lease_id"], include_inactive=True)

        self.assertEqual(recovered, [])
        self.assertEqual(refreshed.status, "running")
        self.assertEqual(active_lease["lease_status"], "active")

    def test_cli_namespace_is_supported(self) -> None:
        parser = build_parser()
        parsed = parser.parse_args(["--once", "--worker-id", "executor", "--node-id", "node-1"])

        self.assertTrue(parsed.once)
        self.assertEqual(parsed.worker_id, "executor")
        self.assertEqual(parsed.node_id, "node-1")


if __name__ == "__main__":
    unittest.main()
