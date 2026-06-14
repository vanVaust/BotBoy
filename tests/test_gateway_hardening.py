from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from botboy.gateway.simple_server import SimpleAPIHandler, SimpleHTTPServer
from botboy.tasks import TASK_STATUS_RUNNING


ROOT = Path(__file__).resolve().parents[1]


def _release_standard_site_packages() -> Path:
    return ROOT / ".release-build-venv" / "Lib" / "site-packages"


def _load_fastapi_primitives():
    try:
        from fastapi import FastAPI  # type: ignore
        from fastapi.testclient import TestClient  # type: ignore
        return FastAPI, TestClient
    except ImportError:
        site_packages = _release_standard_site_packages()
        if not site_packages.exists():
            raise unittest.SkipTest("FastAPI test client is unavailable and no workspace standard env exists.")
        if str(site_packages) not in sys.path:
            sys.path.insert(0, str(site_packages))
        from fastapi import FastAPI  # type: ignore
        from fastapi.testclient import TestClient  # type: ignore
        return FastAPI, TestClient


def _load_create_app():
    site_packages = _release_standard_site_packages()
    if site_packages.exists() and str(site_packages) not in sys.path:
        sys.path.insert(0, str(site_packages))
    from botboy.gateway.server import create_app

    return create_app


def _load_task_router():
    site_packages = _release_standard_site_packages()
    if site_packages.exists() and str(site_packages) not in sys.path:
        sys.path.insert(0, str(site_packages))
    from botboy.gateway.routes_tasks import create_task_router

    return create_task_router


def _make_auth_config(*, enable_auth: bool, rate_limit_enabled: bool = False) -> SimpleNamespace:
    security = SimpleNamespace(
        enable_auth=enable_auth,
        rate_limit_enabled=rate_limit_enabled,
        auth_api_keys_enabled=True,
        jwt_secret="stable-secret-for-gateway-hardening-0123",
    )
    return SimpleNamespace(
        security=security,
        resolve_jwt_secret=lambda: "stable-secret-for-gateway-hardening-0123",
        resolve_api_key_store_path=lambda: "broken-api-keys.db",
        resolve_principal_db_path=lambda: "broken-principals.db",
    )


def _dummy_gateway_bot(config) -> SimpleNamespace:
    async def _process_command(*_args, **_kwargs):
        return {"success": True, "output": "ok", "type": "status"}

    return SimpleNamespace(
        VERSION="0.0-test",
        config=config,
        memory=None,
        skills=None,
        cache=None,
        scheduler=None,
        history=None,
        trace_store=None,
        llm=None,
        router=None,
        archetypes=None,
        metrics=None,
        task_store=None,
        a2a_pilot=None,
        reflection_memory=None,
        delegation_advisor=None,
        _start_time=0.0,
        process_command=_process_command,
        get_monitoring_payload=lambda: {"monitoring": True},
        load_status_snapshot=lambda: {"status": "ok"},
        get_dashboard_payload=lambda mode: {"mode": mode},
        get_reflection_memory_payload=lambda: {"available": False},
        get_delegation_intelligence_payload=lambda: {"available": False},
        get_a2a_pilot_payload=lambda: {"available": False},
        get_worker_payload=lambda worker_id: {"worker_id": worker_id},
        get_task_merge_payload=lambda *_args, **_kwargs: {"merge": {}},
        apply_task_merge_action=lambda *_args, **_kwargs: {"success": True},
    )


class _ReassignStore:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []
        self._task = SimpleNamespace(
            task_id="task-1",
            parent_task_id="root-1",
            owner="worker:planner",
            status=TASK_STATUS_RUNNING,
            principal="release.test",
        )

    def get_task(self, task_id: str):
        if task_id != self._task.task_id:
            return None
        return self._task

    def reassign_task(self, task_id: str, *, worker_id: str, principal: str, request_id: str, run_id: str):
        self.calls.append(
            {
                "task_id": task_id,
                "worker_id": worker_id,
                "principal": principal,
                "request_id": request_id,
                "run_id": run_id,
            }
        )
        self._task = SimpleNamespace(
            task_id=task_id,
            parent_task_id="root-1",
            owner=f"worker:{worker_id}",
            status=TASK_STATUS_RUNNING,
            principal=principal,
        )
        return self._task

    def _get_conn(self):
        raise AssertionError("FastAPI reassign should use TaskStore.reassign_task instead of raw SQL")


class GatewayHardeningTest(unittest.TestCase):
    def tearDown(self) -> None:
        SimpleAPIHandler.principal_store = None
        SimpleAPIHandler.principal_store_factory = None
        SimpleAPIHandler.credential_store = None
        SimpleAPIHandler.credential_store_factory = None
        SimpleAPIHandler.security = None
        SimpleAPIHandler.auth_enabled = False

    def test_fastapi_store_bootstrap_is_fail_closed_when_auth_enabled(self) -> None:
        create_app = _load_create_app()
        bot = _dummy_gateway_bot(_make_auth_config(enable_auth=True))
        api_key_attempts: list[str] = []
        principal_attempts: list[str] = []

        def _secret_store(*, db_path: str):
            api_key_attempts.append(db_path)
            if db_path == "broken-api-keys.db":
                raise OSError("cannot open api key store")
            return SimpleNamespace(close=lambda: None)

        def _principal_store(*, db_path: str):
            principal_attempts.append(db_path)
            if db_path == "broken-principals.db":
                raise OSError("cannot open principal store")
            return SimpleNamespace(close=lambda: None)

        with patch("botboy.gateway.server.SecretStore", side_effect=_secret_store), patch(
            "botboy.gateway.server.PrincipalStore",
            side_effect=_principal_store,
        ):
            app = create_app(bot, host="127.0.0.1", port=8765)
            self.assertIsNone(app.state.bot_api_key_store())
            self.assertIsNone(app.state.bot_principal_store())

        self.assertEqual(api_key_attempts, ["broken-api-keys.db"])
        self.assertEqual(principal_attempts, ["broken-principals.db"])

    def test_fastapi_rejects_nonlocal_bind_without_auth(self) -> None:
        create_app = _load_create_app()
        bot = _dummy_gateway_bot(_make_auth_config(enable_auth=False))

        with self.assertRaises(RuntimeError) as ctx:
            create_app(bot, host="0.0.0.0", port=8765)

        self.assertIn("BotBoy remote readiness: FAIL", str(ctx.exception))
        self.assertIn("Authentication must be enabled", str(ctx.exception))

    def test_fastapi_rejects_nonlocal_bind_without_rate_limit(self) -> None:
        create_app = _load_create_app()
        bot = _dummy_gateway_bot(_make_auth_config(enable_auth=True, rate_limit_enabled=False))

        with self.assertRaises(RuntimeError) as ctx:
            create_app(bot, host="0.0.0.0", port=8765)

        self.assertIn("rate_limit", str(ctx.exception))

    def test_fastapi_allows_nonlocal_bind_with_remote_policy_satisfied(self) -> None:
        create_app = _load_create_app()
        bot = _dummy_gateway_bot(_make_auth_config(enable_auth=True, rate_limit_enabled=True))

        app = create_app(bot, host="0.0.0.0", port=8765)

        self.assertTrue(app.state.security_enabled)

    def test_stdlib_store_bootstrap_is_fail_closed_when_auth_enabled(self) -> None:
        bot = _dummy_gateway_bot(_make_auth_config(enable_auth=True))
        api_key_attempts: list[str] = []
        principal_attempts: list[str] = []

        def _secret_store(*, db_path: str):
            api_key_attempts.append(db_path)
            if db_path == "broken-api-keys.db":
                raise OSError("cannot open api key store")
            return SimpleNamespace(close=lambda: None)

        def _principal_store(*, db_path: str):
            principal_attempts.append(db_path)
            if db_path == "broken-principals.db":
                raise OSError("cannot open principal store")
            return SimpleNamespace(close=lambda: None)

        with patch("botboy.gateway.simple_server.SecretStore", side_effect=_secret_store), patch(
            "botboy.gateway.simple_server.PrincipalStore",
            side_effect=_principal_store,
        ):
            SimpleHTTPServer(bot, host="127.0.0.1", port=0)
            self.assertIsNone(SimpleAPIHandler.principal_store_factory())
            self.assertIsNone(SimpleAPIHandler.credential_store_factory())

        self.assertEqual(api_key_attempts, ["broken-api-keys.db"])
        self.assertEqual(principal_attempts, ["broken-principals.db"])

    def test_stdlib_rejects_nonlocal_bind_without_auth(self) -> None:
        bot = _dummy_gateway_bot(_make_auth_config(enable_auth=False))

        with self.assertRaises(RuntimeError) as ctx:
            SimpleHTTPServer(bot, host="0.0.0.0", port=0)

        self.assertIn("BotBoy remote readiness: FAIL", str(ctx.exception))
        self.assertIn("Authentication must be enabled", str(ctx.exception))

    def test_fastapi_store_bootstrap_does_not_fallback_to_memory_when_auth_disabled(self) -> None:
        create_app = _load_create_app()
        bot = _dummy_gateway_bot(_make_auth_config(enable_auth=False))
        api_key_attempts: list[str] = []
        principal_attempts: list[str] = []

        def _secret_store(*, db_path: str):
            api_key_attempts.append(db_path)
            raise OSError("cannot open api key store")

        def _principal_store(*, db_path: str):
            principal_attempts.append(db_path)
            raise OSError("cannot open principal store")

        with patch("botboy.gateway.server.SecretStore", side_effect=_secret_store), patch(
            "botboy.gateway.server.PrincipalStore",
            side_effect=_principal_store,
        ):
            app = create_app(bot, host="127.0.0.1", port=8765)
            self.assertIsNone(app.state.bot_api_key_store())
            self.assertIsNone(app.state.bot_principal_store())

        self.assertEqual(api_key_attempts, ["broken-api-keys.db"])
        self.assertEqual(principal_attempts, ["broken-principals.db"])

    def test_stdlib_store_bootstrap_does_not_fallback_to_memory_when_auth_disabled(self) -> None:
        bot = _dummy_gateway_bot(_make_auth_config(enable_auth=False))
        api_key_attempts: list[str] = []
        principal_attempts: list[str] = []

        def _secret_store(*, db_path: str):
            api_key_attempts.append(db_path)
            raise OSError("cannot open api key store")

        def _principal_store(*, db_path: str):
            principal_attempts.append(db_path)
            raise OSError("cannot open principal store")

        with patch("botboy.gateway.simple_server.SecretStore", side_effect=_secret_store), patch(
            "botboy.gateway.simple_server.PrincipalStore",
            side_effect=_principal_store,
        ):
            SimpleHTTPServer(bot, host="127.0.0.1", port=0)
            self.assertIsNone(SimpleAPIHandler.principal_store_factory())
            self.assertIsNone(SimpleAPIHandler.credential_store_factory())

        self.assertEqual(api_key_attempts, ["broken-api-keys.db"])
        self.assertEqual(principal_attempts, ["broken-principals.db"])

    def test_fastapi_login_rejects_invalid_credentials_without_development_fallback(self) -> None:
        create_app = _load_create_app()
        bot = _dummy_gateway_bot(_make_auth_config(enable_auth=False))

        class _RejectingPrincipalStore:
            def authenticate(self, username: str, password: str):
                return None

            def has_principals(self) -> bool:
                return False

            def close(self) -> None:
                return None

        with patch("botboy.gateway.server.PrincipalStore", return_value=_RejectingPrincipalStore()):
            app = create_app(bot, host="127.0.0.1", port=8765)
            FastAPI, TestClient = _load_fastapi_primitives()
            with TestClient(app) as client:
                response = client.post("/api/auth/login", json={"username": "mallory", "password": "wrong"})

        self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(response.json()["detail"], "Invalid credentials")

    def test_fastapi_reassign_uses_store_contract(self) -> None:
        FastAPI, TestClient = _load_fastapi_primitives()
        create_task_router = _load_task_router()
        store = _ReassignStore()

        async def _process_command(*_args, **_kwargs):
            return {"success": True, "output": "ok", "type": "status"}

        ctx = SimpleNamespace(
            bot=SimpleNamespace(process_command=_process_command),
            auth_enabled=False,
            authorize=lambda *_args, **_kwargs: None,
            authorize_with_roles=lambda *_args, **_kwargs: ("admin.user", ["admin"]),
            task_store_or_503=lambda: store,
            worker_lookup=lambda: {"planner": {"worker_id": "planner"}, "reviewer": {"worker_id": "reviewer"}},
            decorate_task_record=lambda _store, record, root_records=None: {"task_id": record.task_id, "owner": record.owner},
            task_merge_payload=lambda *_args, **_kwargs: {"merge": {}},
            task_merge_action_payload=lambda *_args, **_kwargs: {"success": True},
            task_list_records=lambda *_args, **_kwargs: ([], 0, []),
            task_metrics=lambda *_args, **_kwargs: {
                "blockers": [],
                "blocked_count": 0,
                "delegated_count": 0,
                "running_count": 0,
                "queued_count": 0,
                "worker_count": 0,
                "handoff_queue_depth": 0,
                "oldest_blocked_age_s": 0,
                "oldest_running_age_s": 0,
                "by_worker": {},
                "by_blocked_kind": {},
            },
            task_workers_payload=lambda *_args, **_kwargs: {"available": True, "workers": []},
            task_records_by_root=lambda *_args, **_kwargs: [],
            direct_child_records=lambda *_args, **_kwargs: [],
            build_task_graph=lambda *_args, **_kwargs: {"nodes": []},
            collect_task_records=lambda *_args, **_kwargs: [],
            worker_id_from_owner=lambda owner: owner.split(":", 1)[-1],
        )

        app = FastAPI()
        app.include_router(create_task_router(ctx))
        with TestClient(app) as client:
            response = client.post("/api/tasks/task-1/reassign", json={"worker_id": "reviewer"})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(store.calls, [
            {
                "task_id": "task-1",
                "worker_id": "reviewer",
                "principal": "admin.user",
                "request_id": "",
                "run_id": "",
            }
        ])
        self.assertEqual(response.json()["task"]["owner"], "worker:reviewer")

    def test_websocket_runtime_errors_surface_as_error_frames(self) -> None:
        FastAPI, TestClient = _load_fastapi_primitives()
        create_app = _load_create_app()
        bot = _dummy_gateway_bot(_make_auth_config(enable_auth=False))

        async def _raise(*_args, **_kwargs):
            raise RuntimeError("ws boom")

        bot.process_command = _raise
        app = create_app(bot, host="127.0.0.1", port=8765)
        with TestClient(app) as client:
            with client.websocket_connect("/ws/chat") as websocket:
                websocket.send_text("status")
                message = websocket.receive_json()

        self.assertEqual(message["type"], "error")
        self.assertIn("ws boom", message["output"])
