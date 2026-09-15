from __future__ import annotations

import unittest
from types import SimpleNamespace

from fastapi import HTTPException

from botboy.gateway import merge_security
from botboy.gateway.app_context import _current_org, _current_principal, _current_roles
from botboy.task_merge_service import TaskMergeService


class _Store:
    def __init__(self, records, children=None):
        self.records = {record.task_id: record for record in records}
        self.children = children or {}

    def get_task(self, task_id):
        return self.records.get(task_id)

    def list_children(self, task_id, limit=200):
        return list(self.children.get(task_id, []))[:limit]


def _service(records, children=None):
    config = SimpleNamespace(security=SimpleNamespace(enable_auth=True))
    store = _Store(records, children)
    bot = SimpleNamespace(config=config, task_store=store)
    service = object.__new__(TaskMergeService)
    service.bot = bot
    return service


def _set_context(principal="alice", org="tenant-a", roles=()):
    _current_principal.set(principal)
    _current_org.set(org)
    _current_roles.set(tuple(roles))


class TestMergeSecurityBoundary(unittest.TestCase):
    def test_merge_target_cannot_cross_tenant(self):
        bob = SimpleNamespace(task_id="bob-task", principal="bob", org_id="tenant-b")
        service = _service([bob])
        _set_context()

        self.assertFalse(merge_security._family_authorized(service, "bob-task"))
        with self.assertRaises(HTTPException) as exc:
            merge_security._guard_task(service, "bob-task")
        self.assertEqual(exc.exception.status_code, 404)

    def test_merge_family_rejects_cross_tenant_child(self):
        parent = SimpleNamespace(task_id="parent", principal="alice", org_id="tenant-a")
        child = SimpleNamespace(task_id="child", principal="bob", org_id="tenant-b")
        service = _service([parent, child], {"parent": [child]})
        _set_context()

        self.assertFalse(merge_security._family_authorized(service, "parent"))

    def test_merge_family_accepts_same_principal_and_org(self):
        parent = SimpleNamespace(task_id="parent", principal="alice", org_id="tenant-a")
        child = SimpleNamespace(task_id="child", principal="alice", org_id="tenant-a")
        service = _service([parent, child], {"parent": [child]})
        _set_context()

        self.assertTrue(merge_security._family_authorized(service, "parent"))

    def test_merge_action_cannot_spoof_audit_principal(self):
        _set_context(principal="alice", org="tenant-a")
        calls = {}

        def original(self, task_id, **kwargs):
            calls.update(kwargs)
            return "ok"

        guarded = merge_security._wrap_apply_action(original)
        service = _service([SimpleNamespace(task_id="task", principal="alice", org_id="tenant-a")])

        result = guarded(service, "task", principal="mallory", action="resolve", key="x")

        self.assertEqual(result, "ok")
        self.assertEqual(calls["principal"], "alice")
