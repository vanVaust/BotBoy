from __future__ import annotations

import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace

from botboy.gateway import app_context
from botboy.gateway.app_context import _TaskStoreAuthorizationProxy
from botboy.gateway.recovery_security import _secure_worker_child_creation
from botboy.security_context import SecurityContext
from botboy.task_security import TaskSecurityStore
from botboy.tasks import TaskStore, TASK_STATUS_QUEUED
from botboy.worker_handoff_service import WorkerHandoffService
from botboy.workers import WorkerRegistry


class WorkerHandoffSecurityBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        root = Path.cwd() / ".botboy-runtime" / "test-worker-handoff-security" / uuid.uuid4().hex
        root.mkdir(parents=True, exist_ok=True)
        self.store = TaskStore(db_path=":memory:", artifact_root=str(root))
        self.worker = WorkerRegistry().list_workers()[0]
        self.bot = SimpleNamespace(
            config=SimpleNamespace(security=SimpleNamespace(enable_auth=True)),
            task_store=self.store,
            workers={self.worker.worker_id: self.worker},
        )
        self.service = WorkerHandoffService(self.bot)
        self._tokens = (
            app_context._current_principal.set("alice"),
            app_context._current_roles.set(("worker",)),
            app_context._current_org.set("tenant-a"),
        )

    def tearDown(self) -> None:
        app_context._current_principal.reset(self._tokens[0])
        app_context._current_roles.reset(self._tokens[1])
        app_context._current_org.reset(self._tokens[2])
        self.store.close()

    def _parent(self):
        parent = self.store.create_task(
            title="parent",
            principal="alice",
            org_id="tenant-a",
            request_id="req-1",
            status=TASK_STATUS_QUEUED,
        )
        TaskSecurityStore(self.store).save(
            SecurityContext.from_legacy(
                principal="alice",
                org_id="tenant-a",
                roles=["worker"],
                request_id="req-1",
                task_id=parent.task_id,
            )
        )
        return parent

    def test_raw_handoff_cannot_replace_parent_principal_or_tenant(self) -> None:
        parent = self._parent()
        original = WorkerHandoffService._create_worker_child_task
        guarded = _secure_worker_child_creation(original)
        child = guarded(
            self.service,
            parent_task=parent,
            worker=self.worker,
            delegated_command="do work",
            principal="attacker",
            child_request_id="req-child",
            attempt_count=1,
        )
        record = self.store.get_task(child.task_id)
        self.assertEqual(record.principal, "alice")
        self.assertEqual(record.org_id, "tenant-a")
        security = TaskSecurityStore(self.store).load(child.task_id)
        self.assertEqual(security.principal_id, "alice")
        self.assertEqual(security.org_id, "tenant-a")

    def test_raw_handoff_rejects_foreign_gateway_principal(self) -> None:
        parent = self._parent()
        token = app_context._current_principal.set("mallory")
        try:
            original = WorkerHandoffService._create_worker_child_task
            guarded = _secure_worker_child_creation(original)
            with self.assertRaises(Exception) as ctx:
                guarded(
                    self.service,
                    parent_task=parent,
                    worker=self.worker,
                    delegated_command="do work",
                    principal="alice",
                    child_request_id="req-child",
                    attempt_count=1,
                )
            self.assertEqual(getattr(ctx.exception, "status_code", None), 404)
        finally:
            app_context._current_principal.reset(token)

    def test_authenticated_proxy_cannot_forward_raw_task_store_mutators(self) -> None:
        proxy = _TaskStoreAuthorizationProxy(self.store, auth_enabled=True)
        with self.assertRaises(Exception) as create_error:
            proxy.create_task(title="forbidden", principal="attacker", org_id="tenant-a")
        self.assertEqual(getattr(create_error.exception, "status_code", None), 403)

        with self.assertRaises(Exception) as update_error:
            proxy.update_task("missing", title="forbidden")
        self.assertEqual(getattr(update_error.exception, "status_code", None), 403)


if __name__ == "__main__":
    unittest.main()
