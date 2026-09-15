import unittest
from types import SimpleNamespace

from fastapi import HTTPException

from botboy.gateway.app_context import _TaskStoreAuthorizationProxy


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
        self.store = FakeStore()
        self.proxy = _TaskStoreAuthorizationProxy(self.store, auth_enabled=True)

    def test_raw_artifact_mutators_are_blocked(self):
        for name in ("add_artifact", "link_artifacts_from_task"):
            with self.subTest(name=name):
                with self.assertRaises(HTTPException) as raised:
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
