from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch

from botboy.security_context import SecurityContext
from botboy.task_security import TaskSecurityStore
from botboy.tasks import TaskStore


class SecurityContextPersistenceTest(unittest.TestCase):
    def test_round_trip_preserves_security_boundary(self):
        context = SecurityContext(
            principal_id="alice",
            org_id="org-a",
            roles=frozenset({"user", "reviewer"}),
            scopes=frozenset({"task:read"}),
            capabilities=frozenset({"task:resume", "filesystem:read"}),
            auth_source="jwt",
            session_id="session-1",
            request_id="req-1",
            task_id="task-1",
            parent_task_id="root-1",
            approval_id="approval-1",
            approval_scope=frozenset({"task:resume"}),
            approval_expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
            authorization_version="7",
        )

        restored = SecurityContext.from_dict(context.to_dict())

        self.assertEqual(restored, context)
        self.assertTrue(restored.has_capability("task:resume"))
        self.assertEqual(restored.org_id, "org-a")
        self.assertEqual(restored.principal_id, "alice")

    def test_expired_approval_is_rejected(self):
        context = SecurityContext(
            principal_id="alice",
            approval_id="approval-1",
            approval_scope=frozenset({"task:resume"}),
            approval_expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
        )

        self.assertFalse(context.approval_valid("task:resume"))

    def test_approval_scope_is_narrow(self):
        context = SecurityContext(
            principal_id="alice",
            approval_id="approval-1",
            approval_scope=frozenset({"task:resume"}),
            approval_expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
        )

        self.assertTrue(context.approval_valid("task:resume"))
        self.assertFalse(context.approval_valid("filesystem:write"))

    def test_child_restriction_cannot_expand_approval_scope(self):
        context = SecurityContext(
            capabilities=frozenset({"task:resume", "filesystem:read"}),
            approval_id="approval-1",
            approval_scope=frozenset({"task:resume", "filesystem:read"}),
            approval_expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
        )

        child = context.restrict_capabilities({"task:resume"})

        self.assertEqual(child.capabilities, frozenset({"task:resume"}))
        self.assertEqual(child.approval_scope, frozenset({"task:resume"}))
        self.assertFalse(child.approval_valid("filesystem:read"))

    def test_concurrent_reductions_cannot_restore_revoked_capabilities(self):
        with TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "tasks.db")
            with TaskStore(db_path=path) as initial:
                task = initial.create_task(title="concurrent security", principal="alice",
                                           org_id="tenant-a", command="status")
                base = SecurityContext(principal_id="alice", org_id="tenant-a",
                                       task_id=task.task_id,
                                       capabilities=frozenset({"task.execute", "task.read"}))
                TaskSecurityStore(initial).save(base)
                stores = [TaskStore(db_path=path) for _ in range(2)]
                boundaries = [TaskSecurityStore(store) for store in stores]
                gate = Barrier(2)
                validate = TaskSecurityStore._validate_transition

                def synchronized_validation(previous, current):
                    validate(previous, current)
                    # Both independent connections validate the same old state.
                    gate.wait(timeout=10)

                def save(index):
                    candidate = replace(base, capabilities=frozenset(
                        {("task.execute", "task.read")[index]}))
                    try:
                        boundaries[index].save(candidate)
                        return candidate, None
                    except ValueError as exc:
                        return candidate, exc

                try:
                    with patch.object(TaskSecurityStore, "_validate_transition", staticmethod(synchronized_validation)):
                        with ThreadPoolExecutor(max_workers=2) as executor:
                            results = list(executor.map(save, range(2)))
                    accepted = [context for context, error in results if error is None]
                    self.assertEqual(len(accepted), 1, results)
                    self.assertEqual(sum(error is not None for _, error in results), 1)
                    persisted = TaskSecurityStore(initial).load(task.task_id)
                    self.assertEqual(persisted, accepted[0])
                    rejected = next(context for context, error in results if error is not None)
                    # Retrying against current state must revalidate and reject
                    # the capability already removed by the successful writer.
                    with self.assertRaisesRegex(ValueError, "capabilities cannot be expanded"):
                        TaskSecurityStore(initial).save(rejected)
                finally:
                    for store in stores:
                        store.close()

    def test_racing_initial_context_cannot_overwrite_the_first_binding(self):
        with TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "tasks.db")
            with TaskStore(db_path=path) as initial:
                task = initial.create_task(title="initial binding", principal="alice",
                                           org_id="tenant-a", command="status")
                stores = [TaskStore(db_path=path) for _ in range(2)]
                boundaries = [TaskSecurityStore(store) for store in stores]
                gate = Barrier(2)

                class SynchronizedConnection:
                    def __init__(self, connection):
                        self.connection = connection

                    def execute(self, query, parameters=()):
                        cursor = self.connection.execute(query, parameters)
                        if query.lstrip().startswith("SELECT security_context_json"):
                            # Materialize the absent row before the other writer
                            # can insert; use two actual SQLite connections.
                            value = cursor.fetchone()
                            gate.wait(timeout=10)
                            return SimpleNamespace(fetchone=lambda: value)
                        return cursor

                    def commit(self):
                        self.connection.commit()

                def save(index):
                    context = SecurityContext(principal_id=("alice", "bob")[index],
                                              org_id="tenant-a", task_id=task.task_id,
                                              capabilities=frozenset({"task.execute"}))
                    connection = stores[index]._get_conn()
                    with patch.object(stores[index], "_get_conn", return_value=SynchronizedConnection(connection)):
                        try:
                            boundaries[index].save(context)
                            return context, None
                        except ValueError as exc:
                            return context, exc

                try:
                    with ThreadPoolExecutor(max_workers=2) as executor:
                        results = list(executor.map(save, range(2)))
                    accepted = [context for context, error in results if error is None]
                    self.assertEqual(len(accepted), 1, results)
                    self.assertEqual(sum(error is not None for _, error in results), 1)
                    self.assertEqual(TaskSecurityStore(initial).load(task.task_id), accepted[0])
                finally:
                    for store in stores:
                        store.close()


if __name__ == "__main__":
    unittest.main()
