from __future__ import annotations

import time
import unittest
from types import SimpleNamespace

from botboy.gateway.simple_server import SimpleAPIHandler, SimpleHTTPServer


def _make_config():
    security = SimpleNamespace(
        enable_auth=False,
        rate_limit_enabled=False,
        auth_api_keys_enabled=True,
        jwt_secret="stable-secret-for-simple-server-lifecycle-0123",
    )
    return SimpleNamespace(
        security=security,
        resolve_jwt_secret=lambda: "stable-secret-for-simple-server-lifecycle-0123",
        resolve_api_key_store_path=lambda: ":memory:",
        resolve_principal_db_path=lambda: ":memory:",
    )


def _make_bot():
    return SimpleNamespace(config=_make_config())


class _ClosableStore:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


class SimpleServerLifecycleTest(unittest.TestCase):
    def tearDown(self) -> None:
        SimpleAPIHandler.botboy = None
        SimpleAPIHandler.web_dir = None
        SimpleAPIHandler.auth = None
        SimpleAPIHandler.rate_limiter = None
        SimpleAPIHandler.principal_store = None
        SimpleAPIHandler.principal_store_factory = None
        SimpleAPIHandler.credential_store = None
        SimpleAPIHandler.credential_store_factory = None
        SimpleAPIHandler.security = None
        SimpleAPIHandler.auth_enabled = False
        SimpleAPIHandler.request_id = ""

    def test_start_is_idempotent_and_stop_is_idempotent(self) -> None:
        server = SimpleHTTPServer(_make_bot(), host="127.0.0.1", port=0)
        server.start(blocking=False)
        self.addCleanup(server.stop)
        time.sleep(0.2)

        first_thread = server._thread
        first_port = server.port
        server.start(blocking=False)

        self.assertIs(server._thread, first_thread)
        self.assertEqual(server.port, first_port)

        principal_store = _ClosableStore()
        credential_store = _ClosableStore()
        SimpleAPIHandler.principal_store = principal_store
        SimpleAPIHandler.credential_store = credential_store

        server.stop()
        server.stop()

        self.assertEqual(principal_store.close_calls, 1)
        self.assertEqual(credential_store.close_calls, 1)
        self.assertIsNone(server._server)
        self.assertIsNone(server._thread)
        self.assertIsNone(SimpleAPIHandler.botboy)
        self.assertIsNone(SimpleAPIHandler.principal_store_factory)
        self.assertIsNone(SimpleAPIHandler.credential_store_factory)
