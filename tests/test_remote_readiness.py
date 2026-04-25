from __future__ import annotations

import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from botboy.command_router import route_command
from botboy.core.config import BotBoyConfig
from botboy.remote_readiness import assess_remote_auth_readiness


class RemoteReadinessTest(unittest.TestCase):
    def _clean_env(self):
        return patch.dict(
            os.environ,
            {
                "BOTBOY_ALLOW_INSECURE_REMOTE": "",
                "BOTBOY_GATEWAY_ALLOW_INSECURE_REMOTE": "",
                "BOTBOY_MCP_ALLOW_INSECURE_REMOTE": "",
                "BOTBOY_CORS_ALLOW_ORIGINS": "",
                "BOTBOY_MCP_HTTP_TOKEN": "",
            },
        )

    def test_local_auth_disabled_is_local_only_not_blocked(self) -> None:
        config = BotBoyConfig()
        config.security.enable_auth = False

        with self._clean_env():
            report = assess_remote_auth_readiness(config, host="127.0.0.1")

        self.assertEqual(report["overall"], "local-only")
        self.assertEqual(report["failed"], 0)
        self.assertTrue(any(check["check_id"] == "jwt_secret" and check["status"] == "warn" for check in report["checks"]))

    def test_remote_auth_disabled_is_blocked(self) -> None:
        config = BotBoyConfig()
        config.security.enable_auth = False

        with self._clean_env():
            report = assess_remote_auth_readiness(config, host="0.0.0.0")

        self.assertEqual(report["overall"], "blocked")
        self.assertGreaterEqual(report["failed"], 1)
        self.assertTrue(
            any(check["check_id"] == "gateway_remote_auth" and check["status"] == "fail" for check in report["checks"])
        )

    def test_remote_auth_ready_with_secret_stores_cors_and_mcp_token(self) -> None:
        config = BotBoyConfig()
        config.security.enable_auth = True
        config.security.jwt_secret = "x" * 32
        config.security.auth_bootstrap_secret = "bootstrap-secret"
        config.security.principal_db_path = "~/.botboy/principals.db"
        config.security.auth_api_key_store_path = "~/.botboy/api_keys.db"

        with self._clean_env(), patch.dict(os.environ, {"BOTBOY_CORS_ALLOW_ORIGINS": "https://ops.example.com"}):
            report = assess_remote_auth_readiness(config, host="0.0.0.0", mcp_http_token="token-123")

        self.assertEqual(report["overall"], "ready")
        self.assertEqual(report["failed"], 0)
        self.assertTrue(all(check["status"] != "fail" for check in report["checks"]))

    def test_security_command_routes_remote_readiness_report(self) -> None:
        config = BotBoyConfig()
        config.security.enable_auth = False
        bot = SimpleNamespace(config=config)

        with self._clean_env():
            result = asyncio.run(route_command(bot, "security remote-readiness 0.0.0.0"))

        self.assertFalse(result["success"])
        self.assertEqual(result["type"], "security")
        self.assertEqual(result["data"]["overall"], "blocked")
        self.assertIn("Remote readiness", result["output"])


if __name__ == "__main__":
    unittest.main()
