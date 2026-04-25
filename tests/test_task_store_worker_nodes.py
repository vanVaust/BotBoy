from __future__ import annotations

import unittest
from pathlib import Path

from botboy.tasks import TaskStore


class TaskStoreWorkerNodesTest(unittest.TestCase):
    def _make_store(self) -> TaskStore:
        temp_root = (Path(__file__).resolve().parents[1] / ".botboy-runtime" / self._testMethodName).resolve()
        temp_root.mkdir(parents=True, exist_ok=True)
        store = TaskStore(
            db_path=str(temp_root / "tasks.db"),
            artifact_root=str(temp_root / "artifacts" / "tasks"),
        )
        self.addCleanup(store.close)
        return store

    def test_register_worker_node_persists_node_and_queue(self) -> None:
        store = self._make_store()

        node = store.register_worker_node(
            node_id="node-alpha",
            worker_id="planner",
            endpoint="http://127.0.0.1:9101",
            capabilities=["decomposition", "routing"],
            max_concurrency=2,
        )

        nodes = store.list_worker_nodes()
        queues = store.list_execution_queues()
        summary = store.worker_node_summary()

        self.assertIsNotNone(node)
        self.assertTrue(any(item["node_id"] == "node-alpha" for item in nodes))
        self.assertTrue(any(item["queue_name"] == node["queue_name"] for item in queues))
        self.assertGreaterEqual(summary.get("node_count", 0), 1)
        self.assertGreaterEqual(summary.get("queue_count", 0), 1)

    def test_heartbeat_updates_last_heartbeat_and_health(self) -> None:
        store = self._make_store()
        store.register_worker_node(
            node_id="node-beta",
            worker_id="executor",
            endpoint="http://127.0.0.1:9102",
            capabilities=["implementation"],
            max_concurrency=1,
        )

        before = store.list_worker_nodes()
        self.assertEqual(len(before), 1)
        self.assertEqual(before[0].get("health", ""), "unknown")

        updated = store.heartbeat_worker_node(
            node_id="node-beta",
            health="healthy",
            load=0.25,
        )
        after = store.list_worker_nodes()

        self.assertIsNotNone(updated)
        self.assertEqual(len(after), 1)
        self.assertEqual(after[0]["node_id"], "node-beta")
        self.assertEqual(after[0].get("health"), "healthy")
        self.assertNotEqual(after[0].get("last_heartbeat_at", ""), "")

    def test_drain_marks_draining(self) -> None:
        store = self._make_store()
        store.register_worker_node(
            node_id="node-gamma",
            worker_id="reviewer",
            endpoint="http://127.0.0.1:9103",
            capabilities=["verification", "merge_review"],
            max_concurrency=1,
        )

        drained = store.drain_worker_node("node-gamma")
        nodes = store.list_worker_nodes()
        summary = store.worker_node_summary()

        self.assertIsNotNone(drained)
        self.assertEqual(nodes[0]["node_id"], "node-gamma")
        self.assertTrue(bool(nodes[0].get("draining")))
        self.assertGreaterEqual(summary.get("draining_count", 0), 1)

    def test_register_and_heartbeat_do_not_reactivate_draining_queue(self) -> None:
        store = self._make_store()
        store.register_worker_node(
            node_id="node-theta",
            worker_id="executor",
            endpoint="http://127.0.0.1:9107",
            capabilities=["implementation"],
            max_concurrency=1,
        )
        store.drain_worker_node("node-theta", reason="maintenance")

        re_registered = store.register_worker_node(
            node_id="node-theta",
            worker_id="executor",
            endpoint="http://127.0.0.1:9107",
            capabilities=["implementation"],
            max_concurrency=1,
        )
        heartbeated = store.heartbeat_worker_node("node-theta", node_status="ready", health="healthy")
        queue = store.get_execution_queue(re_registered["queue_name"])

        self.assertIsNotNone(heartbeated)
        self.assertTrue(bool(re_registered.get("draining")))
        self.assertEqual(re_registered.get("effective_status"), "draining")
        self.assertEqual(heartbeated.get("effective_status"), "draining")
        self.assertIsNotNone(queue)
        self.assertEqual(queue["queue_status"], "draining")

    def test_list_and_summary_return_observability_data(self) -> None:
        store = self._make_store()
        store.register_worker_node(
            node_id="node-delta",
            worker_id="planner",
            endpoint="http://127.0.0.1:9104",
            capabilities=["decomposition"],
            max_concurrency=2,
        )
        store.heartbeat_worker_node("node-delta", health="healthy", load=0.1)
        store.register_worker_node(
            node_id="node-epsilon",
            worker_id="executor",
            endpoint="http://127.0.0.1:9105",
            capabilities=["implementation"],
            max_concurrency=1,
        )
        store.drain_worker_node("node-epsilon")

        nodes = store.list_worker_nodes()
        queues = store.list_execution_queues()
        summary = store.worker_node_summary()

        self.assertEqual(len(nodes), 2)
        self.assertEqual(len(queues), 2)
        self.assertGreaterEqual(summary.get("node_count", 0), 2)
        self.assertGreaterEqual(summary.get("healthy_count", 0), 1)
        self.assertGreaterEqual(summary.get("draining_count", 0), 1)
        self.assertGreaterEqual(summary.get("queue_count", 0), 2)

    def test_reopen_store_preserves_worker_fabric_state(self) -> None:
        temp_root = (Path(__file__).resolve().parents[1] / ".botboy-runtime" / self._testMethodName).resolve()
        temp_root.mkdir(parents=True, exist_ok=True)
        db_path = str(temp_root / "tasks.db")
        artifact_root = str(temp_root / "artifacts" / "tasks")

        store = TaskStore(db_path=db_path, artifact_root=artifact_root)
        store.register_worker_node(
            node_id="node-zeta",
            worker_id="designer",
            endpoint="http://127.0.0.1:9106",
            capabilities=["ux", "control_center"],
            max_concurrency=1,
        )
        store.heartbeat_worker_node("node-zeta", health="healthy", load=0.0)
        store.close()

        reopened = TaskStore(db_path=db_path, artifact_root=artifact_root)
        self.addCleanup(reopened.close)
        nodes = reopened.list_worker_nodes()
        queues = reopened.list_execution_queues()
        summary = reopened.worker_node_summary()

        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0]["node_id"], "node-zeta")
        self.assertEqual(len(queues), 1)
        self.assertGreaterEqual(summary.get("node_count", 0), 1)
        self.assertGreaterEqual(summary.get("queue_count", 0), 1)


if __name__ == "__main__":
    unittest.main()
