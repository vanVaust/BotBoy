from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from botboy.gateway.request_boundary import (
    allowed_hosts,
    check_request_boundary,
    docs_enabled,
    parse_host_header,
)


def check(**updates):
    data = dict(
        method="POST",
        host_header="127.0.0.1:8765",
        origin=None,
        content_type="application/json",
        has_body=True,
        bind_host="127.0.0.1",
        port=8765,
    )
    data.update(updates)
    return check_request_boundary(**data)


class BoundaryDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        for key in ("BOTBOY_ALLOWED_HOSTS", "BOTBOY_CORS_ALLOW_ORIGINS", "BOTBOY_ENABLE_DOCS"):
            os.environ.pop(key, None)

    def test_strict_host_parsing_including_ipv6(self) -> None:
        self.assertEqual(parse_host_header("Localhost:8765"), "localhost")
        self.assertEqual(parse_host_header("[::1]:8765"), "::1")
        for value in (None, "", "localhost@evil", "localhost:wrong", "localhost:99999", "[::1", "::1", "localhost/path"):
            self.assertEqual(parse_host_header(value), "", value)

    def test_local_json_allowed_and_simple_content_types_blocked(self) -> None:
        self.assertTrue(check().allowed)
        for value in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data", "", None):
            with self.subTest(value=value):
                decision = check(content_type=value)
                self.assertFalse(decision.allowed)
                self.assertEqual(decision.status, 415)
        for value in ("application/json; charset=utf-8", "application/vnd.api+json"):
            self.assertTrue(check(content_type=value).allowed)
        self.assertTrue(check(content_type=None, has_body=False).allowed)

    def test_host_rebinding_and_malformed_origin_rejected(self) -> None:
        self.assertEqual(check(host_header="rebind.evil.example:8765").code, "host_not_allowed")
        for value in ("http://evil.example", "null", "https://127.0.0.1.evil.example", "javascript://localhost:8765", "http://evil@localhost:8765", "http://localhost:8765/path"):
            with self.subTest(origin=value):
                decision = check(origin=value)
                self.assertFalse(decision.allowed)
                self.assertEqual(decision.code, "origin_not_allowed")

    def test_same_origin_dev_origin_and_host_allowlist(self) -> None:
        self.assertTrue(check(origin="http://127.0.0.1:8765").allowed)
        self.assertTrue(check(origin="http://localhost:5173", host_header="localhost:8765").allowed)
        with patch.dict(os.environ, {"BOTBOY_ALLOWED_HOSTS": "bot.internal"}):
            self.assertTrue(check(host_header="bot.internal:8765").allowed)
            self.assertFalse(check(host_header="evil.example").allowed)

    def test_same_origin_treats_omitted_port_as_scheme_default(self) -> None:
        with patch.dict(os.environ, {"BOTBOY_ALLOWED_HOSTS": "bot.internal"}):
            self.assertTrue(check(host_header="localhost:80", origin="http://localhost").allowed)
            self.assertTrue(check(host_header="localhost", origin="http://localhost").allowed)
            self.assertTrue(check(host_header="bot.internal", origin="https://bot.internal").allowed)
            self.assertTrue(check(host_header="bot.internal:443", origin="https://bot.internal").allowed)
            self.assertTrue(check(host_header="[::1]:8765", origin="http://[::1]:8765").allowed)
            # bot.internal is an allowed Host but not a configured CORS origin,
            # so only the same-origin rule can admit these requests.
            for host_header, origin in (
                ("bot.internal:8765", "http://bot.internal"),
                ("bot.internal", "http://bot.internal:8765"),
                ("bot.internal:80", "https://bot.internal"),
                ("bot.internal:8765", "http://other.internal:8765"),
            ):
                with self.subTest(host=host_header, origin=origin):
                    self.assertEqual(check(host_header=host_header, origin=origin).code, "origin_not_allowed")

    def test_wildcards_cannot_disable_loopback_browser_boundary(self) -> None:
        with patch.dict(os.environ, {"BOTBOY_ALLOWED_HOSTS": "*", "BOTBOY_CORS_ALLOW_ORIGINS": "*"}):
            self.assertIn("localhost", allowed_hosts("127.0.0.1"))
            self.assertEqual(check(host_header="evil.example").code, "host_not_allowed")
            self.assertEqual(check(origin="http://evil.example").code, "origin_not_allowed")

    def test_remote_bind_checks_explicit_hosts(self) -> None:
        self.assertIsNone(allowed_hosts("0.0.0.0"))
        with patch.dict(os.environ, {"BOTBOY_ALLOWED_HOSTS": "bot.example.com"}):
            self.assertEqual(allowed_hosts("0.0.0.0"), frozenset({"bot.example.com"}))
            self.assertFalse(check(bind_host="0.0.0.0", host_header="evil.example").allowed)

    def test_docs_off_by_default(self) -> None:
        self.assertFalse(docs_enabled())
        with patch.dict(os.environ, {"BOTBOY_ENABLE_DOCS": "true"}):
            self.assertTrue(docs_enabled())


class StdlibBoundaryTests(unittest.TestCase):
    @staticmethod
    def handler(headers):
        from botboy.gateway.simple_server import SimpleAPIHandler

        handler = SimpleAPIHandler.__new__(SimpleAPIHandler)
        handler.headers = headers
        handler.server_host = "127.0.0.1"
        handler.server_port = 8765
        handler.responses = []
        handler._json = lambda body, status=200, headers=None: handler.responses.append((status, body))
        return handler

    def test_simple_post_foreign_origin_and_rebound_host(self) -> None:
        handler = self.handler({"Host": "localhost:8765", "Content-Type": "text/plain", "Content-Length": "12"})
        self.assertFalse(handler._enforce_boundary("POST"))
        self.assertEqual(handler.responses[0][0], 415)
        for headers in ({"Host": "localhost:8765", "Origin": "http://evil.example"}, {"Host": "rebind.evil.example:8765"}):
            handler = self.handler(headers)
            self.assertFalse(handler._enforce_boundary("GET"))
            self.assertEqual(handler.responses[0][0], 403)


class FastApiBoundaryTests(unittest.TestCase):
    @staticmethod
    def client():
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            raise unittest.SkipTest("FastAPI test client unavailable")
        from botboy.gateway.server import create_app
        from tests.test_gateway_hardening import _dummy_gateway_bot, _make_auth_config

        bot = _dummy_gateway_bot(_make_auth_config(enable_auth=False))
        calls = []

        async def process_command(command, **_kwargs):
            calls.append(command)
            return {"success": True, "output": command, "type": "echo"}

        bot.process_command = process_command
        return TestClient(create_app(bot, host="127.0.0.1", port=8765), base_url="http://localhost:8765"), calls

    def test_cross_site_and_non_json_rejected_before_command(self) -> None:
        client, calls = self.client()
        with client:
            for headers, expected in (
                ({"Content-Type": "text/plain", "Origin": "http://evil.example"}, 403),
                ({"Content-Type": "text/plain"}, 415),
                ({"Host": "rebind.evil.example:8765", "Content-Type": "application/json"}, 403),
            ):
                response = client.post("/api/command", content='{"command":"status"}', headers=headers)
                self.assertEqual(response.status_code, expected, response.text)
            self.assertEqual(calls, [])
            self.assertEqual(client.post("/api/command", json={"command": "status"}).status_code, 200)
            self.assertEqual(calls, ["status"])

    def test_foreign_preflight_and_docs_default_rejected(self) -> None:
        client, _ = self.client()
        with client:
            response = client.options("/api/command", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"})
            self.assertEqual(response.status_code, 403, response.text)
            for path in ("/docs", "/redoc", "/openapi.json"):
                self.assertEqual(client.get(path).status_code, 404, path)

    def test_docs_can_be_opted_in_and_csrf_cookie_returns_403(self) -> None:
        from botboy.gateway.csrf import CSRF_COOKIE_NAME

        with patch.dict(os.environ, {"BOTBOY_ENABLE_DOCS": "true"}):
            client, _ = self.client()
            with client:
                self.assertEqual(client.get("/docs").status_code, 200)
                self.assertEqual(client.get("/openapi.json").status_code, 200)
        client, _ = self.client()
        with client:
            client.cookies.set(CSRF_COOKIE_NAME, "csrf-without-header")
            response = client.post(
                "/api/command",
                json={"command": "status"},
            )
            self.assertEqual(response.status_code, 403, response.text)

    def test_websocket_rejects_foreign_origin_and_host(self) -> None:
        from starlette.websockets import WebSocketDisconnect

        client, _ = self.client()
        with client:
            for headers in (
                {"Host": "localhost:8765", "Origin": "http://evil.example"},
                {"Host": "rebind.evil.example:8765"},
            ):
                with self.assertRaises(WebSocketDisconnect):
                    with client.websocket_connect("/ws/chat", headers=headers):
                        pass


if __name__ == "__main__":
    unittest.main()
