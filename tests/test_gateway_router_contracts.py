from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from botboy.__main__ import BotBoy
from botboy.core.config import BotBoyConfig
from botboy.gateway.app_context import GatewayAppContext


ROOT = Path(__file__).resolve().parents[1]


def _release_standard_site_packages() -> Path:
    return ROOT / ".release-build-venv" / "Lib" / "site-packages"


def _load_create_app():
    site_packages = _release_standard_site_packages()
    if site_packages.exists() and str(site_packages) not in sys.path:
        sys.path.insert(0, str(site_packages))
    try:
        from botboy.gateway.server import create_app
        return create_app
    except ImportError:
        if not site_packages.exists():
            raise unittest.SkipTest("FastAPI gateway is unavailable and no release build env exists.")
        from botboy.gateway.server import create_app
        return create_app


def _load_route_factories():
    try:
        from botboy.gateway.routes_auth import create_auth_router
        from botboy.gateway.routes_core import create_core_router
        from botboy.gateway.routes_tasks import create_task_router
        from botboy.gateway.routes_ws import create_ws_router
        return create_auth_router, create_core_router, create_task_router, create_ws_router
    except ImportError:
        site_packages = _release_standard_site_packages()
        if not site_packages.exists():
            raise unittest.SkipTest("FastAPI router modules are unavailable and no release build env exists.")
        if str(site_packages) not in sys.path:
            sys.path.insert(0, str(site_packages))
        from botboy.gateway.routes_auth import create_auth_router
        from botboy.gateway.routes_core import create_core_router
        from botboy.gateway.routes_tasks import create_task_router
        from botboy.gateway.routes_ws import create_ws_router
        return create_auth_router, create_core_router, create_task_router, create_ws_router


def _dummy_bot() -> SimpleNamespace:
    async def _process_command(*args, **kwargs):
        return {"success": True, "output": "ok"}

    bot = SimpleNamespace(
        VERSION="0.0-test",
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
        _start_time=0.0,
        get_monitoring_payload=lambda: {"monitoring": True},
        process_command=_process_command,
    )
    return bot


def _dummy_context(web_dir: Path | None = None) -> GatewayAppContext:
    web_root = web_dir or (ROOT / "web")
    bot = _dummy_bot()
    auth = SimpleNamespace(
        create_pair=lambda principal: SimpleNamespace(
            access_token="token",
            refresh_token="refresh",
            expires_in=3600,
            token_type="bearer",
        ),
        refresh=lambda token: None,
        verify=lambda token: None,
        extract_bearer_token=lambda header: "",
    )
    return GatewayAppContext(
        bot=bot,
        host="127.0.0.1",
        port=8765,
        web_dir=web_root,
        auth=auth,
        auth_enabled=False,
        rate_limiter=None,
        security=SimpleNamespace(auth_api_keys_enabled=True),
        authorize=lambda *args, **kwargs: ("release.test", []),
        authorize_with_roles=lambda *args, **kwargs: ("release.test", ["admin"]),
        approval_context=lambda headers, payload, roles: {},
        require_admin=lambda *args, **kwargs: None,
        get_api_key_store=lambda: None,
        bootstrap_principal_store=lambda: None,
        principal_payload=lambda principal: {"username": getattr(principal, "username", "release.test")},
        task_store_or_503=lambda: None,
        task_detail_payload=lambda task_id: {"task_id": task_id},
        task_merge_payload=lambda task_id: {"task_id": task_id, "merge": True},
        task_merge_action_payload=lambda *args, **kwargs: {"success": True},
        task_list_records=lambda *args, **kwargs: ([], 0, []),
        task_metrics=lambda store: {
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
        task_workers_payload=lambda store: {"available": True, "workers": []},
        task_records_by_root=lambda store, root_task_id: [],
        direct_child_records=lambda records, task_id: [],
        decorate_task_record=lambda *args, **kwargs: {"task_id": "release.test"},
        build_task_graph=lambda *args, **kwargs: {"graph": True},
        collect_task_records=lambda store: [],
        worker_lookup=lambda: {},
        worker_id_from_owner=lambda owner: owner,
        dashboard_payload=lambda mode: {"mode": mode, "dashboard": True},
    )


def _route_paths(router) -> set[str]:
    return {route.path for route in router.routes}


class GatewayRouterContractsTest(unittest.TestCase):
    def _make_bot(self) -> BotBoy:
        temp_root = (ROOT / ".botboy-runtime" / self._testMethodName).resolve()
        temp_root.mkdir(parents=True, exist_ok=True)
        config = BotBoyConfig()
        config.memory.db_path = str(temp_root / "botboy.db")
        config.history.db_path = str(temp_root / "history.db")
        config.scheduler.db_path = str(temp_root / "scheduler.db")
        config.trace.db_path = str(temp_root / "traces.db")
        config.tasks.db_path = str(temp_root / "tasks.db")
        config.tasks.artifact_root = str(temp_root / "artifacts" / "tasks")
        config.security.principal_db_path = str(temp_root / "principals.db")
        config.security.auth_api_key_store_path = str(temp_root / "api_keys.db")
        config.security.jwt_secret_file = str(temp_root / "jwt.secret")
        config.security.enable_auth = False
        config.llm.enabled = False
        bot = BotBoy(config)
        self.assertTrue(bot.initialize())
        self.addCleanup(bot.shutdown)
        return bot

    def test_router_factories_expose_expected_paths(self) -> None:
        create_auth_router, create_core_router, create_task_router, create_ws_router = _load_route_factories()
        ctx = _dummy_context()

        core_paths = _route_paths(create_core_router(ctx))
        task_paths = _route_paths(create_task_router(ctx))
        auth_paths = _route_paths(create_auth_router(ctx))
        ws_paths = _route_paths(create_ws_router(ctx))

        self.assertTrue(
            {
                "/health",
                "/metrics",
                "/",
                "/api/command",
                "/api/status",
                "/api/dashboard",
                "/api/traces",
                "/api/traces/{run_id}",
                "/api/history",
                "/api/history/stats",
                "/api/scheduler",
            }.issubset(core_paths),
            core_paths,
        )
        self.assertTrue(
            {
                "/api/tasks",
                "/api/tasks/blockers",
                "/api/tasks/{task_id}/merge",
                "/api/tasks/{task_id}/merge/actions",
                "/api/tasks/{task_id}",
                "/api/tasks/{task_id}/children",
                "/api/tasks/{task_id}/graph",
                "/api/tasks/{task_id}/events",
                "/api/tasks/{task_id}/artifacts",
                "/api/tasks/{task_id}/resume",
                "/api/tasks/{task_id}/cancel",
                "/api/workers",
                "/api/workers/{worker_id}",
                "/api/v2/workers/nodes",
                "/api/v2/workers/leases",
                "/api/v2/workers/register",
                "/api/v2/workers/heartbeat",
                "/api/v2/workers/leases/acquire",
                "/api/v2/workers/leases/renew",
                "/api/v2/workers/leases/release",
                "/api/v2/workers/{node_id}/drain",
                "/api/tasks/{task_id}/reassign",
            }.issubset(task_paths),
            task_paths,
        )
        self.assertTrue(
            {
                "/api/auth/login",
                "/api/auth/refresh",
                "/api/auth/api-key",
                "/api/principals",
                "/api/principals/{username}",
            }.issubset(auth_paths),
            auth_paths,
        )
        self.assertEqual(ws_paths, {"/ws/chat"})

    def test_create_app_registers_split_gateway_surface(self) -> None:
        create_app = _load_create_app()
        bot = self._make_bot()
        app = create_app(bot, host="127.0.0.1", port=8765)
        paths = {route.path for route in app.routes if hasattr(route, "path")}

        self.assertTrue(
            {
                "/health",
                "/api/status",
                "/api/tasks",
                "/api/v2/workers/nodes",
                "/api/v2/workers/leases",
                "/api/tasks/{task_id}/merge",
                "/api/auth/login",
                "/ws/chat",
            }.issubset(paths),
            paths,
        )

    def test_dashboard_support_contract_matches_bot_methods(self) -> None:
        bot = self._make_bot()
        sentinel_snapshot = {"status": "ok", "source": "support"}
        sentinel_dashboard = {"mode": "fastapi", "source": "support"}
        with patch("botboy.__main__.load_dashboard_status_snapshot", return_value=sentinel_snapshot) as load_snapshot, patch(
            "botboy.__main__.build_dashboard_payload",
            return_value=sentinel_dashboard,
        ) as load_dashboard:
            self.assertEqual(bot.load_status_snapshot(), sentinel_snapshot)
            self.assertEqual(bot.get_dashboard_payload("fastapi"), sentinel_dashboard)
        self.assertTrue(load_snapshot.called)
        self.assertTrue(load_dashboard.called)
