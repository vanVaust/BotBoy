import unittest

from fastapi import HTTPException

import botboy.gateway  # noqa: F401
from botboy.gateway.app_context import (
    _TaskStoreAuthorizationProxy,
    _current_org,
    _current_principal,
    _current_roles,
)


class FakeStore:
    def __init__(self):
        self.nodes = [
            {"node_id": "node-a", "worker_id": "principal-a", "queue_name": "q-a", "metadata": {"org_id": "org-a", "owner_principal": "principal-a"}, "is_healthy": True, "is_stale": False, "effective_status": "ready"},
            {"node_id": "node-b", "worker_id": "principal-b", "queue_name": "q-b", "metadata": {"org_id": "org-b", "owner_principal": "principal-b"}, "is_healthy": True, "is_stale": False, "effective_status": "ready"},
        ]
        self.queues = [
            {"queue_name": "q-a", "worker_id": "principal-a"},
            {"queue_name": "q-b", "worker_id": "principal-b"},
        ]
        self.leases = [
            {"lease_id": "lease-a", "queue_name": "q-a", "task_id": "task-a", "lease_status": "active", "is_recoverable": False},
            {"lease_id": "lease-b", "queue_name": "q-b", "task_id": "task-b", "lease_status": "active", "is_recoverable": False},
        ]

    def list_worker_nodes(self, *args, **kwargs):
        return list(self.nodes)

    def list_execution_queues(self, *args, **kwargs):
        return list(self.queues)

    def list_queue_leases(self, *args, **kwargs):
        return list(self.leases)

    def get_task(self, task_id):
        return type("Task", (), {"task_id": task_id, "principal": task_id.replace("task-", "principal-"), "org_id": task_id.replace("task-", "org-")})()


class WorkerReadBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.principal_token = _current_principal.set("principal-a")
        self.roles_token = _current_roles.set(("worker",))
        self.org_token = _current_org.set("org-a")
        self.proxy = _TaskStoreAuthorizationProxy(FakeStore(), auth_enabled=True)

    def tearDown(self):
        _current_org.reset(self.org_token)
        _current_roles.reset(self.roles_token)
        _current_principal.reset(self.principal_token)

    def test_worker_node_reads_are_tenant_scoped(self):
        nodes = self.proxy.list_worker_nodes()
        self.assertEqual([node["node_id"] for node in nodes], ["node-a"])

    def test_queue_reads_are_tenant_scoped(self):
        queues = self.proxy.list_execution_queues()
        leases = self.proxy.list_queue_leases()
        self.assertEqual([queue["queue_name"] for queue in queues], ["q-a"])
        self.assertEqual([lease["lease_id"] for lease in leases], ["lease-a"])

    def test_non_worker_cannot_use_worker_read_surfaces(self):
        _current_roles.set(("user",))
        for method in ("list_worker_nodes", "list_execution_queues", "list_queue_leases", "worker_node_summary", "queue_summary"):
            with self.subTest(method=method):
                with self.assertRaises(HTTPException) as raised:
                    getattr(self.proxy, method)()
                self.assertEqual(raised.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
