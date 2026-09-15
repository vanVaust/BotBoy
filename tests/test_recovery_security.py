import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from botboy.gateway.app_context import (
    _TaskStoreAuthorizationProxy,
    _current_org,
    _current_principal,
    _current_roles,
)
from botboy.security_context import SecurityContext
from botboy.task_security import TaskSecurityStore
from botboy.tasks import TaskStore, TASK_STATUS_RUNNING


class RecoverySecurityTests(unittest.TestCase):
    def setUp(self):
        self.db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db.close()
        self.store = TaskStore(self.db.name)
        self.tokens = []

    def tearDown(self):
        for var, token in reversed(self.tokens):
            var.reset(token)
        try:
            self.store._get_conn().close()
        except Exception:
            pass

    def set_identity(self, principal, org, roles):
        self.tokens.append((_current_principal, _current_principal.set(principal)))
        self.tokens.append((_current_org, _current_org.set(org)))
        self.tokens.append((_current_roles, _current_roles.set(tuple(roles))))

    def proxy(self):
        return _TaskStoreAuthorizationProxy(self.store, auth_enabled=True)

    def create_task(self, *, principal, org, parent="", worker="worker-a", status="queued"):
        return self.store.create_task(
            title="security test",
            principal=principal,
            org_id=org,
            parent_task_id=parent,
            delegated_to_worker=worker,
            status=status,
            heartbeat_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat() if status == TASK_STATUS_RUNNING else "",
        )

    def test_foreign_tenant_stale_task_cannot_be_recovered(self):
        parent = self.create_task(principal="alice", org="tenant-b")
        child = self.create_task(principal="alice", org="tenant-b", parent=parent.task_id, status=TASK_STATUS_RUNNING)
        self.set_identity("alice", "tenant-a", ["worker"])

        result = self.proxy().recover_stale_worker_task(child.task_id, principal="alice")
        self.assertIsNone(result)
        self.assertEqual(self.store.get_task(child.task_id).status, TASK_STATUS_RUNNING)

    def test_recovery_cannot_spoof_event_principal(self):
        parent = self.create_task(principal="alice", org="tenant-a")
        child = self.create_task(principal="alice", org="tenant-a", parent=parent.task_id, status=TASK_STATUS_RUNNING)
        self.set_identity("alice", "tenant-a", ["worker"])

        result = self.proxy().recover_stale_worker_task(child.task_id, principal="attacker")
        self.assertIsNotNone(result)
        events = self.store.get_events(child.task_id, limit=10)
        recovery = next(event for event in events if event.event_type == "lease_recovered")
        self.assertEqual(recovery.principal, "alice")

    def test_worker_cannot_reassign_task(self):
        task = self.create_task(principal="alice", org="tenant-a")
        self.set_identity("alice", "tenant-a", ["worker"])
        with self.assertRaises(Exception):
            self.proxy().reassign_task(task.task_id, worker_id="worker-b")
        self.assertEqual(self.store.get_task(task.task_id).delegated_to_worker, "worker-a")

    def test_child_task_inherits_tenant_and_security_context(self):
        parent = self.create_task(principal="alice", org="tenant-a")
        security = SecurityContext.from_legacy(
            principal="alice",
            org_id="tenant-a",
            roles=["worker"],
            task_id=parent.task_id,
        )
        TaskSecurityStore(self.store).save(security)
        self.set_identity("alice", "tenant-a", ["worker"])

        child = self.proxy().create_child_task(
            parent.task_id,
            worker_id="worker-b",
            title="child",
            principal="attacker",
        )
        stored = self.store.get_task(child.task_id)
        self.assertEqual(stored.org_id, "tenant-a")
        self.assertEqual(stored.principal, "alice")
        child_security = TaskSecurityStore(self.store).load(child.task_id)
        self.assertIsNotNone(child_security)
        self.assertEqual(child_security.org_id, "tenant-a")
        self.assertEqual(child_security.principal_id, "alice")
        self.assertEqual(child_security.parent_task_id, parent.task_id)


if __name__ == "__main__":
    unittest.main()
