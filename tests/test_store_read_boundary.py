import unittest

import botboy.gateway  # noqa: F401
from botboy.gateway.app_context import (
    _TaskStoreAuthorizationProxy,
    _current_org,
    _current_principal,
    _current_roles,
)


class Record:
    def __init__(self, task_id, principal, org_id, *, blocked_by_task_id="", root_task_id="root"):
        self.task_id = task_id
        self.principal = principal
        self.org_id = org_id
        self.blocked_by_task_id = blocked_by_task_id
        self.root_task_id = root_task_id

    def to_dict(self):
        return {"task_id": self.task_id, "principal": self.principal, "org_id": self.org_id}


class FakeStore:
    def __init__(self):
        self.records = {
            "task-a": Record("task-a", "principal-a", "org-a"),
            "task-b": Record("task-b", "principal-b", "org-b"),
            "block-a": Record("block-a", "principal-a", "org-a"),
            "block-b": Record("block-b", "principal-b", "org-b"),
        }
        self.records["task-a"].blocked_by_task_id = "block-a"
        self.worker_leases = [
            {"task_id": "task-a", "worker_id": "executor", "is_stale": False},
            {"task_id": "task-b", "worker_id": "executor", "is_stale": False},
        ]

    def get_task(self, task_id):
        return self.records.get(task_id)

    def list_blockers(self, *, limit=100):
        return [self.records["block-a"], self.records["block-b"]][:limit]

    def task_graph(self, task_id):
        return {
            "root_task_id": "root",
            "task": self.records[task_id].to_dict(),
            "root": self.records[task_id].to_dict(),
            "children": [self.records["task-a"].to_dict(), self.records["task-b"].to_dict()],
            "tasks": [self.records["task-a"].to_dict(), self.records["task-b"].to_dict()],
        }

    def list_worker_leases(self, *, limit=100, only_stale=False, lease_timeout_s=900):
        return list(self.worker_leases)[:limit]


class StoreReadBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.principal_token = _current_principal.set("principal-a")
        self.roles_token = _current_roles.set(("worker",))
        self.org_token = _current_org.set("org-a")
        self.proxy = _TaskStoreAuthorizationProxy(FakeStore(), auth_enabled=True)

    def tearDown(self):
        _current_org.reset(self.org_token)
        _current_roles.reset(self.roles_token)
        _current_principal.reset(self.principal_token)

    def test_list_blockers_is_tenant_scoped(self):
        self.assertEqual([item.task_id for item in self.proxy.list_blockers()], ["block-a"])

    def test_task_graph_is_tenant_scoped(self):
        graph = self.proxy.task_graph("task-a")
        self.assertEqual([item["task_id"] for item in graph["tasks"]], ["task-a"])
        self.assertEqual([item["task_id"] for item in graph["children"]], ["task-a"])

    def test_worker_leases_are_tenant_scoped(self):
        leases = self.proxy.list_worker_leases()
        self.assertEqual([lease["task_id"] for lease in leases], ["task-a"])

    def test_unknown_task_graph_is_empty(self):
        self.assertEqual(self.proxy.task_graph("task-b")["task"], None)


if __name__ == "__main__":
    unittest.main()
