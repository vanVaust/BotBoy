import unittest
from types import SimpleNamespace

from botboy.gateway.app_context import _TaskStoreAuthorizationProxy
from botboy.gateway import store_read_security
from botboy.gateway.app_context import _current_org, _current_principal, _current_roles


class FakeStore:
    artifact_root = "."

    def __init__(self, records):
        self.records = {record.task_id: record for record in records}
        self.children = {}

    def get_task(self, task_id):
        return self.records.get(task_id)

    def list_children(self, task_id, *, limit=100):
        return list(self.children.get(task_id, []))[:limit]

    def get_blocked_tasks(self, task_id, *, limit=100):
        return [record for record in self.records.values() if record.blocked_by_task_id == task_id][:limit]


class TaskRelationshipSecurityTests(unittest.TestCase):
    def setUp(self):
        self.owner = SimpleNamespace(
            task_id="owner",
            org_id="org-a",
            principal="alice",
            blocked_by_task_id="",
        )
        self.child = SimpleNamespace(
            task_id="child",
            org_id="org-a",
            principal="alice",
            blocked_by_task_id="",
        )
        self.foreign = SimpleNamespace(
            task_id="foreign",
            org_id="org-b",
            principal="bob",
            blocked_by_task_id="",
        )
        self.store = FakeStore([self.owner, self.child, self.foreign])
        self.store.children["owner"] = [self.child, self.foreign]
        _current_principal.set("alice")
        _current_org.set("org-a")
        _current_roles.set(())
        self.proxy = _TaskStoreAuthorizationProxy(self.store, auth_enabled=True)
        store_read_security.install()

    def test_list_children_filters_cross_tenant_records(self):
        children = self.proxy.list_children("owner")
        self.assertEqual([record.task_id for record in children], ["child"])

    def test_foreign_parent_is_not_enumerable(self):
        self.assertEqual(self.proxy.list_children("foreign"), [])
        self.assertEqual(self.proxy.get_children("foreign"), [])

    def test_blocked_tasks_filter_cross_tenant_records(self):
        self.owner.blocked_by_task_id = "blocker"
        blocker = SimpleNamespace(
            task_id="blocker",
            org_id="org-a",
            principal="alice",
            blocked_by_task_id="owner",
        )
        foreign = SimpleNamespace(
            task_id="foreign-blocker",
            org_id="org-b",
            principal="bob",
            blocked_by_task_id="owner",
        )
        self.store.records.update({"blocker": blocker, "foreign-blocker": foreign})
        blocked = self.proxy.get_blocked_tasks("owner")
        self.assertEqual([record.task_id for record in blocked], ["blocker"])


if __name__ == "__main__":
    unittest.main()
