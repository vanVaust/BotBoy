import unittest

from botboy.gateway.app_context import _TaskStoreAuthorizationProxy, _current_principal, _current_roles


class _Store:
    artifact_root = "."

    def __init__(self):
        self.nodes = {}

    def get_worker_node(self, node_id):
        return self.nodes.get(node_id)

    def register_worker_node(self, **kwargs):
        node = {
            "node_id": kwargs["node_id"],
            "metadata": kwargs.get("metadata", {}),
        }
        self.nodes[node["node_id"]] = node
        return node

    def heartbeat_worker_node(self, node_id, **kwargs):
        return self.nodes.get(node_id)

    def drain_worker_node(self, node_id, **kwargs):
        return self.nodes.get(node_id)


class WorkerNodeSecurityTests(unittest.TestCase):
    def setUp(self):
        self.store = _Store()

    def _proxy(self, principal, roles):
        _current_principal.set(principal)
        _current_roles.set(tuple(roles))
        return _TaskStoreAuthorizationProxy(self.store, auth_enabled=True)

    def test_worker_cannot_register_node(self):
        proxy = self._proxy("worker-a", ["worker"])
        with self.assertRaises(Exception):
            proxy.register_worker_node(node_id="node-1", worker_id="worker-a")

    def test_owner_is_bound_and_other_worker_cannot_mutate(self):
        admin = self._proxy("admin", ["admin"])
        admin.register_worker_node(node_id="node-1", worker_id="worker-a")
        owner = self._proxy("worker-a", ["worker"])
        self.assertIsNotNone(owner.heartbeat_worker_node("node-1"))
        other = self._proxy("worker-b", ["worker"])
        self.assertIsNone(other.heartbeat_worker_node("node-1"))
        self.assertIsNone(other.drain_worker_node("node-1"))

    def test_admin_can_reassign_existing_node(self):
        admin = self._proxy("admin", ["admin"])
        admin.register_worker_node(node_id="node-1", worker_id="worker-a")
        admin.register_worker_node(node_id="node-1", worker_id="worker-b")
        self.assertEqual(self.store.nodes["node-1"]["metadata"]["owner_principal"], "admin")


if __name__ == "__main__":
    unittest.main()
