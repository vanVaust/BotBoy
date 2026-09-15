import unittest

from botboy.gateway.app_context import _TaskStoreAuthorizationProxy, _current_org, _current_principal, _current_roles


class FakeQueueStore:
    artifact_root = "."

    def __init__(self):
        self.nodes = {
            "node-a": {"node_id": "node-a", "worker_id": "worker-a", "queue_name": "queue-a", "metadata": {"org_id": "tenant-a", "owner_principal": "alice"}},
            "node-b": {"node_id": "node-b", "worker_id": "worker-b", "queue_name": "queue-b", "metadata": {"org_id": "tenant-b", "owner_principal": "bob"}},
        }
        self.queues = {
            "queue-a": {"queue_name": "queue-a", "worker_id": "worker-a", "metadata": {"org_id": "tenant-a"}},
            "queue-b": {"queue_name": "queue-b", "worker_id": "worker-b", "metadata": {"org_id": "tenant-b"}},
        }
        self.leases = {
            "lease-a": {"lease_id": "lease-a", "queue_name": "queue-a", "node_id": "node-a", "metadata": {"org_id": "tenant-a", "principal": "alice"}, "lease_status": "active", "is_recoverable": False},
            "lease-b": {"lease_id": "lease-b", "queue_name": "queue-b", "node_id": "node-b", "metadata": {"org_id": "tenant-b", "principal": "bob"}, "lease_status": "active", "is_recoverable": False},
        }
        self.tasks = {
            "task-a": type("Task", (), {"task_id": "task-a", "principal": "alice", "org_id": "tenant-a"})(),
            "task-b": type("Task", (), {"task_id": "task-b", "principal": "bob", "org_id": "tenant-b"})(),
        }
        self.acquired = []

    def get_worker_node(self, node_id): return self.nodes.get(node_id)
    def get_execution_queue(self, queue_name): return self.queues.get(queue_name)
    def get_task(self, task_id): return self.tasks.get(task_id)
    def acquire_queue_lease(self, **kwargs):
        self.acquired.append(kwargs)
        return {"lease_id": "new", "metadata": kwargs["metadata"]}
    def get_queue_lease(self, lease_id): return self.leases.get(lease_id)
    def renew_queue_lease(self, lease_id, *args, **kwargs): return self.leases.get(lease_id)
    def release_queue_lease(self, lease_id, *args, **kwargs): return self.leases.get(lease_id)
    def list_worker_nodes(self, **kwargs): return list(self.nodes.values())
    def list_execution_queues(self, **kwargs): return list(self.queues.values())
    def list_queue_leases(self, **kwargs): return list(self.leases.values())
    def worker_node_summary(self): return {"available": True, "node_count": 2}
    def queue_summary(self): return {"available": True, "queue_count": 2}


class QueueTenantSecurityTests(unittest.TestCase):
    def setUp(self):
        self.store = FakeQueueStore()
        self.proxy = _TaskStoreAuthorizationProxy(self.store, auth_enabled=True)
        _current_org.set("tenant-a")
        _current_principal.set("alice")
        _current_roles.set(("worker",))

    def test_worker_can_acquire_only_owned_node_in_current_tenant(self):
        lease = self.proxy.acquire_queue_lease(queue_name="queue-a", node_id="node-a", task_id="task-a", principal="alice")
        self.assertIsNotNone(lease)
        self.assertEqual(self.store.acquired[-1]["metadata"]["org_id"], "tenant-a")
        self.assertEqual(self.store.acquired[-1]["metadata"]["principal"], "alice")
        with self.assertRaises(Exception):
            self.proxy.acquire_queue_lease(queue_name="queue-b", node_id="node-b", task_id="task-b", principal="alice")

    def test_admin_cannot_cross_tenant(self):
        _current_roles.set(("admin",))
        with self.assertRaises(Exception):
            self.proxy.acquire_queue_lease(queue_name="queue-b", node_id="node-b", task_id="task-b", principal="admin")

    def test_lease_renew_and_release_are_tenant_bound(self):
        self.assertIsNotNone(self.proxy.renew_queue_lease("lease-a", principal="alice"))
        with self.assertRaises(Exception):
            self.proxy.renew_queue_lease("lease-b", principal="alice")
        self.assertIsNotNone(self.proxy.release_queue_lease("lease-a", principal="alice"))
        with self.assertRaises(Exception):
            self.proxy.release_queue_lease("lease-b", principal="alice")

    def test_lists_filter_to_current_tenant(self):
        self.assertEqual(["node-a"], [item["node_id"] for item in self.proxy.list_worker_nodes()])
        self.assertEqual(["queue-a"], [item["queue_name"] for item in self.proxy.list_execution_queues()])
        self.assertEqual(["lease-a"], [item["lease_id"] for item in self.proxy.list_queue_leases()])


if __name__ == "__main__":
    unittest.main()
