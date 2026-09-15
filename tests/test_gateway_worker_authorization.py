import unittest

from botboy.gateway.app_context import (
    GatewayAppContext,
    _TaskStoreAuthorizationProxy,
    _current_principal,
    _current_roles,
)


class _FakeStore:
    def __init__(self):
        self.calls = []
        self.leases = {
            "lease-1": {
                "lease_id": "lease-1",
                "metadata": {"principal": "alice"},
            }
        }

    def get_task(self, task_id):
        return type("Task", (), {"principal": "alice"})()

    def get_queue_lease(self, lease_id):
        return self.leases.get(lease_id)

    def renew_queue_lease(self, lease_id, **kwargs):
        self.calls.append(("renew", lease_id, kwargs))
        return {"lease_id": lease_id}

    def release_queue_lease(self, lease_id, **kwargs):
        self.calls.append(("release", lease_id, kwargs))
        return {"lease_id": lease_id}

    def register_worker_node(self, **kwargs):
        self.calls.append(("register", kwargs))
        return kwargs


class GatewayWorkerAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.store = _FakeStore()

    def _proxy(self, principal, roles):
        _current_principal.set(principal)
        _current_roles.set(tuple(roles))
        return _TaskStoreAuthorizationProxy(self.store, auth_enabled=True)

    def test_foreign_lease_cannot_be_renewed_or_released(self):
        proxy = self._proxy("mallory", ["worker"])
        self.assertIsNone(proxy.renew_queue_lease("lease-1"))
        self.assertIsNone(proxy.release_queue_lease("lease-1"))
        self.assertEqual([], self.store.calls)

    def test_owner_can_renew_and_release(self):
        proxy = self._proxy("alice", ["worker"])
        self.assertEqual("lease-1", proxy.renew_queue_lease("lease-1")["lease_id"])
        self.assertEqual("lease-1", proxy.release_queue_lease("lease-1")["lease_id"])
        self.assertEqual(2, len(self.store.calls))

    def test_normal_user_cannot_control_worker_fleet(self):
        proxy = self._proxy("alice", ["user"])
        with self.assertRaises(Exception):
            proxy.register_worker_node(node_id="n1", worker_id="executor")


if __name__ == "__main__":
    unittest.main()
