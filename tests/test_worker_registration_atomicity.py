"""Worker-node and queue registration share an atomic tenant boundary."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from botboy.gateway.app_context import (
    _TaskStoreAuthorizationProxy, _current_org, _current_principal, _current_roles,
)
from botboy.gateway.errors import HTTPException
from botboy.tasks import TaskStore


class WorkerRegistrationAtomicityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="botboy-registration-atomic-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.store = TaskStore(str(root / "tasks.db"), artifact_root=str(root / "artifacts"))
        self.addCleanup(self.store.close)

    def proxy(self, tenant, role="admin"):
        _current_org.set(tenant)
        _current_principal.set("admin-" + tenant)
        _current_roles.set((role,))
        return _TaskStoreAuthorizationProxy(self.store, auth_enabled=True)

    def race_registration(self, same_node):
        barrier = threading.Barrier(2)
        local = threading.local()
        method = "get_worker_node" if same_node else "get_execution_queue"
        original = getattr(self.store, method)

        def initial_read(identifier):
            result = original(identifier)
            if not getattr(local, "checked", False):
                local.checked = True
                barrier.wait(timeout=10)
            return result

        def register(tenant):
            node = "same-node" if same_node else "node-" + tenant
            queue = "queue-" + tenant if same_node else "same-queue"
            try:
                result = self.proxy(tenant).register_worker_node(
                    node_id=node, worker_id="executor", queue_name=queue,
                )
                return tenant, 200, result
            except HTTPException as exc:
                return tenant, exc.status_code, None

        # Both callers have already observed absence before either writes.
        with patch.object(self.store, method, side_effect=initial_read):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(register, ("tenant-a", "tenant-b")))
        self.assertEqual(sorted(status for _, status, _ in results), [200, 404], results)
        winner = next(tenant for tenant, status, _ in results if status == 200)
        nodes = self.store.list_worker_nodes()
        queues = self.store.list_execution_queues()
        self.assertEqual(len(nodes), 1, nodes)
        self.assertEqual(len(queues), 1, queues)
        self.assertEqual(nodes[0]["metadata"]["org_id"], winner)
        self.assertEqual(queues[0]["metadata"]["org_id"], winner)
        self.assertEqual(nodes[0]["metadata"]["owner_principal"], "admin-" + winner)

    def test_concurrent_first_node_registration_cannot_take_over_other_tenant(self):
        self.race_registration(same_node=True)

    def test_concurrent_first_queue_registration_cannot_take_over_other_tenant(self):
        self.race_registration(same_node=False)

    def test_same_tenant_update_and_explicit_system_administration_work(self):
        proxy = self.proxy("tenant-a")
        proxy.register_worker_node(node_id="node", worker_id="executor")
        updated = proxy.register_worker_node(node_id="node", worker_id="executor", display_name="updated")
        self.assertEqual(updated["display_name"], "updated")
        self.assertEqual(updated["metadata"]["owner_principal"], "admin-tenant-a")
        system = self.proxy("platform", role="system")
        updated = system.register_worker_node(
            node_id="node", worker_id="executor", metadata={"org_id": "tenant-b"},
        )
        self.assertEqual(updated["metadata"]["org_id"], "tenant-b")
        self.assertEqual(updated["queue"]["metadata"]["org_id"], "tenant-b")

    def test_registration_error_rolls_back_node_queue_and_capabilities(self):
        class InvalidCapability:
            def __str__(self):
                raise ValueError("test-only capability failure")

        for memory in (False, True):
            with self.subTest(memory=memory):
                store = TaskStore(":memory:") if memory else self.store
                try:
                    original = store.register_worker_node(
                        node_id="node", worker_id="executor", capabilities=["original"],
                        metadata={"org_id": "tenant-a", "owner_principal": "owner"},
                    )
                    with self.assertRaisesRegex(ValueError, "test-only capability failure"):
                        store.register_worker_node(
                            node_id="node", worker_id="executor", queue_name="new-queue",
                            capabilities=[InvalidCapability()], expected_org_id="tenant-a",
                            metadata={"org_id": "tenant-a", "owner_principal": "replacement"},
                        )
                    self.assertIsNone(store.get_execution_queue("new-queue"))
                    persisted = store.get_worker_node("node")
                    self.assertEqual(persisted["metadata"], original["metadata"])
                    self.assertEqual(persisted["queue_name"], original["queue_name"])
                    self.assertEqual(persisted["capabilities"], ["original"])
                finally:
                    if memory:
                        store.close()
