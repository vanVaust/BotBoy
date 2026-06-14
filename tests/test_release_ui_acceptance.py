from __future__ import annotations

import http.client
import importlib.util
import json
import sys
import time
import unittest
from pathlib import Path

from botboy.__main__ import BotBoy
from botboy.core.config import BotBoyConfig
from botboy.gateway.security import default_bind_host
from botboy.gateway.simple_server import SimpleHTTPServer


ROOT = Path(__file__).resolve().parents[1]


def _release_standard_site_packages() -> Path:
    return ROOT / ".release-build-venv" / "Lib" / "site-packages"


def _load_fastapi_testclient():
    try:
        from fastapi.testclient import TestClient  # type: ignore
        return TestClient
    except ImportError:
        site_packages = _release_standard_site_packages()
        if not site_packages.exists():
            raise unittest.SkipTest("FastAPI test client is unavailable and no workspace standard env exists.")
        if str(site_packages) not in sys.path:
            sys.path.insert(0, str(site_packages))
        from fastapi.testclient import TestClient  # type: ignore
        return TestClient


def _load_fastapi_gateway_factory():
    site_packages = _release_standard_site_packages()
    if site_packages.exists() and str(site_packages) not in sys.path:
        sys.path.insert(0, str(site_packages))
    from botboy.gateway.server import create_app

    return create_app


class ReleaseUiAcceptanceTest(unittest.TestCase):
    def _make_bot(self) -> BotBoy:
        temp_root = (ROOT / ".botboy-runtime" / "ui-acceptance" / self._testMethodName).resolve()
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

    def test_control_center_assets_expose_required_smoke_markers(self) -> None:
        index_html = (ROOT / "botboy" / "web" / "index.html").read_text(encoding="utf-8")
        renderer_js = (ROOT / "botboy" / "web" / "control_center_renderer.js").read_text(encoding="utf-8")

        for text in (
            "Operations Control Center",
            'data-smoke="control-banner"',
            'data-smoke="latest-merge-review"',
            "Known Gaps",
            "Latest Merge Review",
            "Replay diffs",
            "Replay diff codes",
            "Queue leases",
            "Recoverable queue leases",
        ):
            self.assertIn(text, index_html)

        for text in (
            "renderControlCenter",
            "status_snapshot",
            'data-smoke="control-banner"',
            'data-smoke="latest-merge-review"',
            "Operations Control Center",
            "Replay diffs",
            "Queue leases",
        ):
            self.assertIn(text, renderer_js)

    def test_dashboard_payload_matches_control_center_contract_script(self) -> None:
        bot = self._make_bot()
        payload = bot.get_dashboard_payload("stdlib")
        self._assert_dashboard_contract(payload, suffix="stdlib-bot")

    def test_dashboard_api_live_contract_matches_control_center_surface(self) -> None:
        bot = self._make_bot()

        create_app = _load_fastapi_gateway_factory()
        TestClient = _load_fastapi_testclient()
        app = create_app(bot, host=default_bind_host(), port=8765)
        with TestClient(app) as client:
            fastapi_response = client.get("/api/dashboard")

        self.assertEqual(fastapi_response.status_code, 200, fastapi_response.text)
        self._assert_dashboard_contract(fastapi_response.json(), suffix="fastapi-http")

        server = SimpleHTTPServer(bot, host=default_bind_host(), port=0)
        server.start(blocking=False)
        self.addCleanup(server.stop)
        time.sleep(0.2)

        connection = http.client.HTTPConnection(server.host, server.port, timeout=10)
        try:
            connection.request("GET", "/api/dashboard")
            response = connection.getresponse()
            body = response.read().decode("utf-8")
        finally:
            connection.close()

        self.assertEqual(response.status, 200, body)
        self._assert_dashboard_contract(json.loads(body), suffix="stdlib-http")

    def test_fastapi_and_stdlib_serve_control_center_surface(self) -> None:
        bot = self._make_bot()

        def assert_control_center_response(status: int, body: str) -> None:
            self.assertEqual(status, 200, body)
            self.assertIn("Operations Control Center", body)
            self.assertIn('data-smoke="control-banner"', body)
            self.assertIn('data-smoke="latest-merge-review"', body)

        def assert_dashboard_response(status: int, body: str) -> None:
            self.assertEqual(status, 200, body)
            self.assertIn("BotBoy", body)
            self.assertIn("ReactDOM.createRoot", body)

        def assert_renderer_response(status: int, body: str) -> None:
            self.assertEqual(status, 200, body)
            self.assertIn("renderControlCenter", body)
            self.assertIn("status_snapshot", body)

        create_app = _load_fastapi_gateway_factory()
        TestClient = _load_fastapi_testclient()
        app = create_app(bot, host=default_bind_host(), port=8765)
        with TestClient(app) as client:
            index = client.get("/")
            dashboard = client.get("/static/dashboard.html")
            renderer = client.get("/static/control_center_renderer.js")

        assert_control_center_response(index.status_code, index.text)
        assert_dashboard_response(dashboard.status_code, dashboard.text)
        assert_renderer_response(renderer.status_code, renderer.text)

        server = SimpleHTTPServer(bot, host=default_bind_host(), port=0)
        server.start(blocking=False)
        self.addCleanup(server.stop)
        time.sleep(0.2)

        def request(path: str) -> tuple[int, str]:
            connection = http.client.HTTPConnection(server.host, server.port, timeout=10)
            try:
                connection.request("GET", path)
                response = connection.getresponse()
                body = response.read().decode("utf-8")
                return response.status, body
            finally:
                connection.close()

        index_status, index_body = request("/")
        dashboard_status, dashboard_body = request("/dashboard.html")
        renderer_status, renderer_body = request("/control_center_renderer.js")

        assert_control_center_response(index_status, index_body)
        assert_dashboard_response(dashboard_status, dashboard_body)
        assert_renderer_response(renderer_status, renderer_body)

    def _assert_dashboard_contract(self, payload: dict, *, suffix: str) -> None:
        for key in (
            "status_snapshot",
            "traces",
            "monitoring",
            "operations_summary",
            "system_readiness",
        ):
            self.assertIn(key, payload)

        snapshot = payload["status_snapshot"]
        for key in (
            "completed_capabilities",
            "open_priorities",
            "deferred_items",
            "eval_replay",
            "ui_status",
        ):
            self.assertIn(key, snapshot)

        contract_payload = {
            "snapshot": snapshot,
            "traces": payload.get("traces", {}),
            "monitoring": payload.get("monitoring", {}),
        }
        contract = payload.get("control_center_contract", {})
        self.assertIn("contract_version", contract)
        self.assertIn("segments", contract)
        self.assertIn("write_set", contract)
        self.assertIn("queue_lease", contract["segments"])
        self.assertIn("replay", contract["segments"])
        self.assertIn("incident", contract["segments"])
        self.assertIn("replay_diff", contract["segments"]["replay"])
        temp_root = (ROOT / ".botboy-runtime" / "ui-acceptance-contracts" / self._testMethodName).resolve()
        temp_root.mkdir(parents=True, exist_ok=True)
        payload_path = temp_root / f"dashboard_payload-{suffix}.json"
        payload_path.write_text(json.dumps(contract_payload, indent=2, sort_keys=True), encoding="utf-8")

        script_path = ROOT / "skills" / "internal_skills" / "botboy-control-center-smokes" / "scripts" / "check_dashboard_contract.py"
        spec = importlib.util.spec_from_file_location("check_dashboard_contract", script_path)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        result = module.main([str(script_path), str(payload_path)])
        self.assertEqual(result, 0)
