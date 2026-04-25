from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace

from botboy.gateway.auth import AuthPrincipal, JWTAuth
from botboy.gateway.rate_limit import RateLimiter
from botboy.gateway.secrets import PrincipalStore, SecretStore
from botboy.gateway.simple_server_support import (
    SimpleServerSupport,
    apply_simple_handler_state,
    build_simple_handler_state,
    close_simple_handler_stores,
    reset_simple_handler_state,
)
from botboy.tasks import (
    TASK_STATUS_QUEUED,
    TASK_STATUS_RUNNING,
    TASK_STATUS_WAITING_APPROVAL,
    TaskStore,
)


def _temp_root(prefix: str) -> Path:
    path = Path.cwd() / ".botboy-runtime" / "test-simple-server-support" / f"{prefix}-{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _make_handler():
    handler_cls = type("Handler", (), {})
    handler = handler_cls()
    handler.headers = {}
    handler.client_address = ("203.0.113.9", 12345)
    handler.rate_limiter = None
    handler.auth_enabled = False
    handler.auth = None
    handler.principal_store = None
    handler.credential_store = None
    handler.security = SimpleNamespace(
        auth_bootstrap_secret="",
        auth_bootstrap_user="admin",
        auth_bootstrap_role="admin",
    )
    handler.botboy = None
    handler._json_calls = []

    def _json(payload, status=200, headers=None):
        handler._json_calls.append({"payload": payload, "status": status, "headers": headers or {}})

    handler._json = _json
    return handler


class AccessAndPrincipalSupportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = _temp_root("access")
        self.handler = _make_handler()
        self.support = SimpleServerSupport(self.handler)

    def tearDown(self) -> None:
        for attr in ("principal_store", "credential_store"):
            store = getattr(self.handler, attr, None)
            if store:
                store.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_client_identity_rate_limit_and_access(self) -> None:
        self.handler.headers = {
            "x-forwarded-for": "198.51.100.1, 203.0.113.2",
            "authorization": "",
        }
        self.handler.rate_limiter = RateLimiter(max_requests=1, window_seconds=3600)
        self.handler.rate_limiter.check("198.51.100.1")

        self.assertEqual(self.support.client_identity(), "198.51.100.1")
        self.assertTrue(self.support.enforce_rate_limit("198.51.100.1"))
        self.assertEqual(self.handler._json_calls[-1]["status"], 429)
        self.assertEqual(self.handler._json_calls[-1]["payload"]["error"], "Rate limit exceeded")

        self.handler._json_calls.clear()
        self.handler.rate_limiter = None
        self.assertEqual(self.support.ensure_access(require_auth=False), "198.51.100.1")

    def test_auth_and_approval_context(self) -> None:
        self.handler.auth_enabled = True
        self.handler.auth = JWTAuth("x" * 32)
        pair = self.handler.auth.create_pair(
            AuthPrincipal(principal_id="admin-user", roles=["admin"], principal_type="admin")
        )
        self.handler.headers = {"authorization": f"Bearer {pair.access_token}"}

        principal_id, roles = self.support.ensure_access_with_roles(require_auth=True)
        self.assertEqual(principal_id, "admin-user")
        self.assertEqual(roles, ["admin"])
        self.assertEqual(self.support.current_token_info().principal_id, "admin-user")
        self.assertTrue(self.support.require_admin())

        approval = self.support.approval_context({"approval": False}, roles=["admin"])
        self.assertTrue(approval["granted"])
        self.assertEqual(approval["reason"], "admin_role")

        self.handler.headers = {}
        self.handler._json_calls.clear()
        self.assertIsNone(self.support.ensure_access(require_auth=True))
        self.assertEqual(self.handler._json_calls[-1]["status"], 401)

    def test_store_bootstrap_and_principal_payload(self) -> None:
        api_key_store = SecretStore(db_path=str(self.root / "api_keys.sqlite"))
        principal_store = PrincipalStore(db_path=str(self.root / "principals.sqlite"))

        self.addCleanup(api_key_store.close)
        self.addCleanup(principal_store.close)

        type(self.handler).principal_store_factory = lambda: api_key_store
        type(self.handler).credential_store_factory = lambda: principal_store
        self.handler.botboy = SimpleNamespace(
            config=SimpleNamespace(resolve_bootstrap_secret=lambda: "bootstrap-secret")
        )
        self.handler.security.auth_bootstrap_secret = ""

        self.assertIs(self.support.get_api_key_store(), api_key_store)
        self.assertIs(self.support.get_credential_store(), principal_store)
        self.assertIs(self.support.bootstrap_principal_store(), principal_store)
        self.assertTrue(principal_store.has_principals())

        principal = principal_store.get_principal("admin")
        self.assertIsNotNone(principal)
        payload = self.support.principal_payload(principal)
        self.assertEqual(payload["principal_id"], "admin")
        self.assertEqual(payload["role"], "admin")
        self.assertFalse(payload["disabled"])

    def test_build_apply_close_and_reset_handler_state(self) -> None:
        bot = SimpleNamespace(
            config=SimpleNamespace(
                security=SimpleNamespace(
                    enable_auth=False,
                    rate_limit_enabled=True,
                    rate_limit_requests=5,
                    rate_limit_window_seconds=30,
                    auth_api_keys_enabled=True,
                    jwt_secret="x" * 32,
                ),
                resolve_jwt_secret=lambda: "x" * 32,
                resolve_api_key_store_path=lambda: str(self.root / "api_keys-state.sqlite"),
                resolve_principal_db_path=lambda: str(self.root / "principals-state.sqlite"),
            )
        )
        handler_cls = type("TempHandler", (), {})
        state = build_simple_handler_state(bot, host="127.0.0.1", port=8765)
        self.assertTrue(callable(state["principal_store_factory"]))
        self.assertTrue(callable(state["credential_store_factory"]))
        apply_simple_handler_state(handler_cls, state)
        principal_store = handler_cls.principal_store_factory()
        credential_store = handler_cls.credential_store_factory()
        handler_cls.principal_store = principal_store
        handler_cls.credential_store = credential_store
        close_simple_handler_stores(handler_cls)
        self.assertIsNone(handler_cls.principal_store)
        self.assertIsNone(handler_cls.credential_store)
        reset_simple_handler_state(handler_cls)
        self.assertIsNone(handler_cls.botboy)
        self.assertFalse(handler_cls.auth_enabled)


class TaskDecorationSupportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = _temp_root("tasks")
        self.handler = _make_handler()
        self.store = TaskStore(
            db_path=str(self.root / "tasks.sqlite"),
            artifact_root=str(self.root / "artifacts"),
        )
        self.addCleanup(self.store.close)
        self.support = SimpleServerSupport(self.handler)

        self.parent = self.store.create_task(
            title="Parent task",
            owner="worker:planner",
            principal="alice",
            status=TASK_STATUS_RUNNING,
            summary="Parent summary",
        )
        self.queued = self.store.create_task(
            title="Queued task",
            owner="worker:reviewer",
            principal="bob",
            status=TASK_STATUS_QUEUED,
            summary="Queued summary",
        )
        self.child = self.store.create_task(
            title="Child task",
            owner="worker:executor",
            principal="alice",
            status=TASK_STATUS_WAITING_APPROVAL,
            summary="Child summary",
            parent_task_id=self.parent.task_id,
            root_task_id=self.parent.task_id,
        )
        self.child_record = self.store.get_task(self.child.task_id)
        self.handler.botboy = SimpleNamespace(
            config=SimpleNamespace(resolve_bootstrap_secret=lambda: "bootstrap-secret"),
            task_store=self.store,
            get_dashboard_payload=self._get_dashboard_payload,
            get_task_merge_payload=self._get_task_merge_payload,
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _get_task_merge_payload(self, task_id: str, record=None) -> dict:
        if task_id == self.child.task_id:
            return {
                "available": True,
                "review_status": "pending",
                "review_state": "pending",
                "resolution_policy": "prefer_non_null",
                "review_pending_keys": ["summary"],
                "pending_child_ids": [],
                "override_count": 1,
                "override_active": True,
            }
        return {"available": False}

    def _get_dashboard_payload(self, mode: str = "stdlib") -> dict:
        tasks = dict(self.store.summary())
        tasks.update(
            {
                "latest_merge_task_id": self.child.task_id,
                "latest_merge_resolution_policy": "prefer_non_null",
                "merge_review_ready_count": 1,
                "merge_conflict_task_count": 1,
                "merge_conflict_total": 2,
                "merge_actionable_count": 1,
                "merge_override_task_count": 1,
                "merge_override_total": 1,
                "merge_resolution_policies": {"prefer_non_null": 1},
            }
        )
        return {"mode": mode, "tasks": tasks, "operations_summary": {"task_total": tasks["total"]}}

    def test_task_decoration_and_filters(self) -> None:
        root_records = self.support.collect_task_records(self.store, root_task_id=self.parent.task_id)
        decorated = self.support.decorate_task_record(self.store, self.child_record, root_records=root_records)
        self.assertEqual(decorated["delegation_status"], "blocked_on_child")
        self.assertEqual(decorated["merge"]["resolution_policy"], "prefer_non_null")
        self.assertIn("resolve_many", decorated["merge"]["available_review_actions"])
        self.assertTrue(self.support.task_matches_merge_review(self.child_record, "pending", root_records=root_records))

        page, total, records = self.support.task_list_records(
            self.store,
            limit=10,
            offset=0,
            status=None,
            principal=None,
            request_id=None,
            root_task_id=self.parent.task_id,
            merge_review="pending",
        )
        self.assertEqual(total, 1)
        self.assertEqual([item.task_id for item in page], [self.child.task_id])
        self.assertEqual([item.task_id for item in records], [self.child.task_id, self.parent.task_id])

        metrics = self.support.task_metrics(self.store)
        self.assertEqual(metrics["blocked_count"], 1)
        self.assertEqual(metrics["running_count"], 1)
        self.assertEqual(metrics["queued_count"], 1)
        self.assertEqual(metrics["by_worker"]["executor"], 1)
        self.assertEqual(metrics["by_worker"]["planner"], 1)
        self.assertEqual(metrics["by_worker"]["reviewer"], 1)

    def test_task_workers_and_dashboard_payload(self) -> None:
        workers = self.support.task_workers_payload(self.store)
        self.assertTrue(workers["available"])
        self.assertEqual(workers["worker_count"], 5)
        executor = next(item for item in workers["registry"] if item["worker_id"] == "executor")
        self.assertEqual(executor["summary"]["task_count"], 1)
        self.assertEqual(executor["summary"]["blocked_count"], 1)
        self.assertEqual(executor["summary"]["latest_task_id"], self.child.task_id)

        payload = self.support.dashboard_payload("stdlib")
        self.assertEqual(payload["mode"], "stdlib")
        self.assertTrue(payload["handoffs"]["available"])
        self.assertEqual(payload["tasks"]["latest_task_id"], self.child.task_id)
        self.assertTrue(payload["tasks"]["latest"]["merge"]["available"])
        self.assertEqual(payload["operations_summary"]["latest_merge_task_id"], self.child.task_id)
        self.assertEqual(payload["operations_summary"]["latest_merge_resolution_policy"], "prefer_non_null")
