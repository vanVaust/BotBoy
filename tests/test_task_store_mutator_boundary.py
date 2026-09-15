import unittest
from types import SimpleNamespace

from fastapi import HTTPException

# Import the gateway package so its security boundary patches are installed.
import botboy.gateway  # noqa: F401,E402
from botboy.gateway.app_context import _TaskStoreAuthorizationProxy, _current_principal, _current_roles


class FakeStore:
    artifact_root = "."

    def __init__(self):
        self.called = []

    def __getattr__(self, name):
        def _mutator(*args, **kwargs):
            self.called.append((name, args, kwargs))
            return SimpleNamespace(task_id="created")

        return _mutator


class TaskStoreMutatorBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.principal_token = _current_principal.set("")
        self.roles_token = _current_roles.set(())
        self.store = FakeStore()
        self.proxy = _TaskStoreAuthorizationProxy(self.store, auth_enabled=True)

    def tearDown(self):
        _current_roles.reset(self.roles_token)
        _current_principal.reset(self.principal_token)

    def test_raw_artifact_mutators_are_blocked(self):
        for name in ("add_artifact", "link_artifacts_from_task"):
            with self.subTest(name=name):
                with self.assertRaises(HTTPException) as raised:
                    if name == "link_artifacts_from_task":
                        getattr(self.proxy, name)("task-a", "task-b")
                    else:
                        getattr(self.proxy, name)("task-a")
                self.assertEqual(raised.exception.status_code, 403)
                self.assertEqual(self.store.called, [])

    def test_existing_mutators_remain_blocked(self):
        for name in ("create_task", "start_task", "finish_task", "add_event"):
            with self.subTest(name=name):
                with self.assertRaises(HTTPException) as raised:
                    getattr(self.proxy, name)("task-a")
                self.assertEqual(raised.exception.status_code, 403)
                self.assertEqual(self.store.called, [])


if __name__ == "__main__":
    unittest.main()
