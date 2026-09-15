import tempfile
import unittest

from botboy.security_context import SecurityContext
from botboy.task_security import TaskSecurityStore
from botboy.tasks import TaskStore


class TaskSecurityStoreTests(unittest.TestCase):
    def test_round_trip_preserves_security_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(db_path=f"{tmp}/botboy.db")
            try:
                security = TaskSecurityStore(store)
                context = SecurityContext(
                    principal_id="alice",
                    org_id="org-a",
                    roles=frozenset({"operator"}),
                    scopes=frozenset({"task:read"}),
                    capabilities=frozenset({"filesystem:read"}),
                    auth_source="jwt",
                    session_id="session-1",
                    request_id="request-1",
                    task_id="task-1",
                    parent_task_id="",
                    authorization_version="7",
                )
                store.create_task(title="security test", principal="alice", org_id="org-a")
                task_id = store.list_tasks(limit=1)[0][0].task_id
                context = context.with_task(task_id)
                security.save(context)

                restored = security.load(task_id)
                self.assertIsNotNone(restored)
                self.assertEqual(restored.principal_id, "alice")
                self.assertEqual(restored.org_id, "org-a")
                self.assertEqual(restored.capabilities, frozenset({"filesystem:read"}))
                self.assertEqual(restored.authorization_version, "7")
            finally:
                store.close()

    def test_missing_context_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(db_path=f"{tmp}/botboy.db")
            try:
                security = TaskSecurityStore(store)
                self.assertIsNone(security.load("does-not-exist"))
            finally:
                store.close()

    def test_malformed_context_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(db_path=f"{tmp}/botboy.db")
            try:
                security = TaskSecurityStore(store)
                task = store.create_task(title="security test")
                conn = store._get_conn()
                conn.execute(
                    "INSERT INTO task_security_contexts "
                    "(task_id, security_context_json, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?)",
                    (task.task_id, "[]", store._now(), store._now()),
                )
                conn.commit()
                self.assertIsNone(security.load(task.task_id))
            finally:
                store.close()

    def _context(self, task_id: str, **changes) -> SecurityContext:
        values = {
            "principal_id": "alice",
            "org_id": "org-a",
            "roles": frozenset({"operator"}),
            "scopes": frozenset({"tasks:read"}),
            "capabilities": frozenset({"task.read", "task.execute"}),
            "task_id": task_id,
            "authorization_version": "1",
        }
        values.update(changes)
        return SecurityContext(**values)

    def test_existing_context_cannot_change_owner_or_tenant(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(db_path=f"{tmp}/botboy.db")
            try:
                task = store.create_task(title="security test", principal="alice", org_id="org-a")
                security = TaskSecurityStore(store)
                security.save(self._context(task.task_id))
                with self.assertRaises(ValueError):
                    security.save(self._context(task.task_id, principal_id="bob"))
                with self.assertRaises(ValueError):
                    security.save(self._context(task.task_id, org_id="org-b"))
            finally:
                store.close()

    def test_existing_context_cannot_gain_privileges(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(db_path=f"{tmp}/botboy.db")
            try:
                task = store.create_task(title="security test", principal="alice", org_id="org-a")
                security = TaskSecurityStore(store)
                security.save(self._context(task.task_id))
                with self.assertRaises(ValueError):
                    security.save(self._context(task.task_id, capabilities=frozenset({"task.read", "task.execute", "admin"})))
                with self.assertRaises(ValueError):
                    security.save(self._context(task.task_id, roles=frozenset({"operator", "admin"})))
                with self.assertRaises(ValueError):
                    security.save(self._context(task.task_id, scopes=frozenset({"tasks:read", "tasks:write"})))
            finally:
                store.close()

    def test_authorization_version_cannot_move_backwards(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(db_path=f"{tmp}/botboy.db")
            try:
                task = store.create_task(title="security test", principal="alice", org_id="org-a")
                security = TaskSecurityStore(store)
                security.save(self._context(task.task_id, authorization_version="7"))
                with self.assertRaises(ValueError):
                    security.save(self._context(task.task_id, authorization_version="6"))
            finally:
                store.close()

    def test_existing_approval_binding_cannot_be_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(db_path=f"{tmp}/botboy.db")
            try:
                task = store.create_task(title="security test", principal="alice", org_id="org-a")
                security = TaskSecurityStore(store)
                security.save(
                    self._context(
                        task.task_id,
                        approval_id="approval-a",
                        approval_scope=frozenset({"task.execute"}),
                    )
                )
                with self.assertRaises(ValueError):
                    security.save(
                        self._context(
                            task.task_id,
                            approval_id="approval-b",
                            approval_scope=frozenset({"task.execute"}),
                        )
                    )
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
