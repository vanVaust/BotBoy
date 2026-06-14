from __future__ import annotations

import unittest

from botboy.gateway.simple_routing import (
    resolve_delete_route,
    resolve_get_route,
    resolve_post_route,
)


class SimpleRoutingTest(unittest.TestCase):
    def test_resolve_get_exact_and_dynamic_routes(self) -> None:
        self.assertEqual(resolve_get_route("/api/status"), ("_handle_status", None))
        self.assertEqual(resolve_get_route("/api/tasks/task-1"), ("_handle_task_detail", "task-1"))
        self.assertEqual(resolve_get_route("/api/tasks/task-1/merge"), ("_handle_task_merge", "task-1"))
        self.assertEqual(resolve_get_route("/api/workers/reviewer"), ("_handle_worker_detail", "reviewer"))
        self.assertEqual(resolve_get_route("/api/v2/workers/nodes"), ("_handle_worker_nodes_get", None))
        self.assertEqual(resolve_get_route("/api/v2/workers/leases"), ("_handle_worker_leases_get", None))
        self.assertEqual(resolve_get_route("/unknown"), (None, None))

    def test_resolve_post_exact_and_dynamic_routes(self) -> None:
        self.assertEqual(resolve_post_route("/api/command"), ("_handle_command", None))
        self.assertEqual(resolve_post_route("/api/tasks/task-2/resume"), ("_handle_task_resume", "task-2"))
        self.assertEqual(resolve_post_route("/api/v2/workers/register"), ("_handle_worker_node_register", None))
        self.assertEqual(resolve_post_route("/api/v2/workers/heartbeat"), ("_handle_worker_node_heartbeat", None))
        self.assertEqual(resolve_post_route("/api/v2/workers/leases/acquire"), ("_handle_worker_lease_acquire", None))
        self.assertEqual(resolve_post_route("/api/v2/workers/leases/renew"), ("_handle_worker_lease_renew", None))
        self.assertEqual(resolve_post_route("/api/v2/workers/leases/release"), ("_handle_worker_lease_release", None))
        self.assertEqual(
            resolve_post_route("/api/v2/workers/node-1/drain"),
            ("_handle_worker_node_drain", "node-1"),
        )
        self.assertEqual(
            resolve_post_route("/api/tasks/task-2/merge/actions"),
            ("_handle_task_merge_action", "task-2"),
        )
        self.assertEqual(resolve_post_route("/unknown"), (None, None))

    def test_resolve_delete_routes(self) -> None:
        self.assertEqual(
            resolve_delete_route("/api/principals/operator"),
            ("_handle_principals_delete", "operator"),
        )
        self.assertEqual(resolve_delete_route("/api/tasks/task-1"), (None, None))


if __name__ == "__main__":
    unittest.main()
