from __future__ import annotations

import unittest
from types import SimpleNamespace
from pathlib import Path

from botboy.gateway.app_context import GatewayAppContext, current_gateway_principal


class FakeStore:
    def __init__(self) -> None:
        self.records = {
            "task-a": SimpleNamespace(task_id="task-a", principal="alice"),
            "task-b": SimpleNamespace(task_id="task-b", principal="bob"),
        }
        self.list_calls = []
        self.cancelled = []

    def get_task(self, task_id: str):
        return self.records.get(task_id)

    def list_tasks(self, **kwargs):
        self.list_calls.append(dict(kwargs))
        principal = kwargs.get("principal")
        rows = list(self.records.values())
        if principal:
            rows = [row for row in rows if row.principal == principal]
        return rows, len(rows)

    def get_events(self, task_id: str, **kwargs):
        return [{"task_id": task_id}]

    def get_artifacts(self, task_id: str, **kwargs):
        return [{"task_id": task_id}]

    def cancel_task(self, task_id: str, **kwargs):
        self.cancelled.append((task_id, kwargs))
        return self.records.get(task_id)


class GatewayTaskAuthorizationTest(unittest.TestCase):
    def _context(self, store: FakeStore, *, auth_enabled: bool = True, roles=()):
        def authorize(*_args, **_kwargs):
            return "alice"

        def authorize_with_roles(*_args, **_kwargs):
            return "alice", list(roles)

        return GatewayAppContext(
            bot=SimpleNamespace(),
            host="127.0.0.1",
            port=8765,
            web_dir=Path("."),
            auth=None,
            auth_enabled=auth_enabled,
            rate_limiter=None,
            security=None,
            authorize=authorize,
            authorize_with_roles=authorize_with_roles,
            approval_context=lambda *_args, **_kwargs: {},
            require_admin=lambda *_args, **_kwargs: None,
            get_api_key_store=lambda: None,
            bootstrap_principal_store=lambda: None,
            principal_payload=lambda value: value,
            task_store_or_503=lambda **_kwargs: store,
            task_detail_payload=lambda _task_id: {},
            task_merge_payload=lambda _task_id: {},
            task_merge_action_payload=lambda *_args, **_kwargs: {},
            task_list_records=lambda *_args, **_kwargs: ([], 0, []),
            task_metrics=lambda _store: {},
            task_workers_payload=lambda _store: {},
            task_records_by_root=lambda _store, _root: [],
            direct_child_records=lambda _records, _parent: [],
            decorate_task_record=lambda *_args, **_kwargs: {},
            build_task_graph=lambda *_args, **_kwargs: {},
            collect_task_records=lambda *_args, **_kwargs: [],
            worker_lookup=lambda: {},
            worker_id_from_owner=lambda _owner: "",
            dashboard_payload=lambda _mode: {},
        )

    def test_non_admin_cannot_resolve_foreign_task(self):
        store = FakeStore()
        ctx = self._context(store)
        ctx.authorize({}, "127.0.0.1", require_auth=True)
        scoped = ctx.task_store_or_503()

        self.assertIsNotNone(scoped.get_task("task-a"))
        self.assertIsNone(scoped.get_task("task-b"))
        self.assertEqual(current_gateway_principal(), "alice")

    def test_non_admin_list_is_forced_to_authenticated_principal(self):
        store = FakeStore()
        ctx = self._context(store)
        ctx.authorize({}, "127.0.0.1", require_auth=True)
        scoped = ctx.task_store_or_503()

        rows, total = scoped.list_tasks(principal="bob")

        self.assertEqual([row.task_id for row in rows], ["task-a"])
        self.assertEqual(total, 1)
        self.assertEqual(store.list_calls[-1]["principal"], "alice")

    def test_foreign_task_events_and_artifacts_are_hidden(self):
        store = FakeStore()
        ctx = self._context(store)
        ctx.authorize({}, "127.0.0.1", require_auth=True)
        scoped = ctx.task_store_or_503()

        self.assertEqual(scoped.get_events("task-a"), [{"task_id": "task-a"}])
        self.assertEqual(scoped.get_artifacts("task-a"), [{"task_id": "task-a"}])
        self.assertEqual(scoped.get_events("task-b"), [])
        self.assertEqual(scoped.get_artifacts("task-b"), [])

    def test_foreign_task_cannot_be_cancelled(self):
        store = FakeStore()
        ctx = self._context(store)
        ctx.authorize({}, "127.0.0.1", require_auth=True)
        scoped = ctx.task_store_or_503()

        self.assertIsNone(scoped.cancel_task("task-b", principal="alice"))
        self.assertEqual(store.cancelled, [])

    def test_admin_can_access_foreign_tasks(self):
        store = FakeStore()
        ctx = self._context(store, roles=("admin",))
        ctx.authorize_with_roles({}, "127.0.0.1", require_auth=True)
        scoped = ctx.task_store_or_503()

        self.assertIsNotNone(scoped.get_task("task-b"))
        rows, total = scoped.list_tasks(principal="bob")
        self.assertEqual([row.task_id for row in rows], ["task-b"])
        self.assertEqual(total, 1)
