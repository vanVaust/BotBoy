import unittest

from botboy.gateway.app_context import _TaskStoreAuthorizationProxy


class FakeStore:
    def future_mutator(self, *args, **kwargs):
        return "mutated"

    def future_read(self, *args, **kwargs):
        return "read"


class TaskStoreProxySurfaceTests(unittest.TestCase):
    def test_authenticated_proxy_fails_closed_for_unknown_methods(self):
        proxy = _TaskStoreAuthorizationProxy(FakeStore(), auth_enabled=True)

        with self.assertRaises(AttributeError):
            proxy.future_mutator("task-a")

        with self.assertRaises(AttributeError):
            proxy.future_read()

    def test_unauthenticated_proxy_retains_legacy_passthrough(self):
        proxy = _TaskStoreAuthorizationProxy(FakeStore(), auth_enabled=False)

        self.assertEqual(proxy.future_mutator("task-a"), "mutated")
        self.assertEqual(proxy.future_read(), "read")


if __name__ == "__main__":
    unittest.main()
