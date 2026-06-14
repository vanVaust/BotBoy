from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from botboy.core.config import BotBoyConfig
from botboy.gateway.security import default_bind_host, origin_allowed, resolve_cors_origins
from botboy.gateway.security import build_remote_readiness_report, is_local_bind_host, is_remote_bind_host


ROOT = Path(__file__).resolve().parents[1]


class ReleaseCliGatewayTest(unittest.TestCase):
    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["BOTBOY_HOME"] = str((ROOT / ".botboy-runtime" / "cli-gateway-tests").resolve())
        env.pop("BOTBOY_CONFIG", None)
        env.pop("BOTBOY_ENABLE_AUTH", None)
        env.pop("BOTBOY_JWT_SECRET", None)
        env.pop("BOTBOY_CORS_ALLOW_ORIGINS", None)
        return env

    def test_cli_help_mentions_release_commands(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "botboy", "--help"],
            cwd=ROOT,
            env=self._env(),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("evals", result.stdout)
        self.assertIn("test", result.stdout)
        self.assertIn("serve", result.stdout)

    def test_cli_eval_summary_uses_bundled_release_assets(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "botboy", "evals", "--summary"],
            cwd=ROOT,
            env=self._env(),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Manifest: release_smoke", result.stdout)
        self.assertIn("Pass rate: 1.000", result.stdout)

    def test_cli_modules_reports_core_optional_dependencies(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "botboy", "modules"],
            cwd=ROOT,
            env=self._env(),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        lowered = result.stdout.lower()
        for expected in ("fastapi", "uvicorn", "aiohttp", "websockets", "yaml"):
            self.assertIn(expected, lowered)

    def test_gateway_security_defaults_are_localhost_only(self) -> None:
        self.assertEqual(default_bind_host(), "127.0.0.1")
        self.assertTrue(is_local_bind_host("127.0.0.1"))
        self.assertTrue(is_local_bind_host("localhost"))
        self.assertTrue(is_remote_bind_host("0.0.0.0"))
        origins = resolve_cors_origins()
        self.assertTrue(origins)
        self.assertTrue(
            all("localhost" in origin or "127.0.0.1" in origin or "::1" in origin for origin in origins)
        )
        self.assertFalse(any("0.0.0.0" in origin for origin in origins))
        self.assertTrue(origin_allowed("http://[::1]:8765", host="::1", port=8765))

    def test_remote_readiness_allows_local_auth_optional(self) -> None:
        config = BotBoyConfig()
        config.security.enable_auth = False
        report = build_remote_readiness_report(config, host="127.0.0.1", port=8765)
        self.assertTrue(report["ok"])
        self.assertFalse(report["remote"])

    def test_remote_readiness_rejects_enabled_auth_without_secret_even_locally(self) -> None:
        config = BotBoyConfig()
        config.security.enable_auth = True
        config.security.jwt_secret = ""
        config.security.jwt_secret_file = str(ROOT / ".botboy-runtime" / "missing.jwt.secret")
        report = build_remote_readiness_report(config, host="127.0.0.1", port=8765)
        self.assertFalse(report["ok"])
        failed = {check["name"] for check in report["checks"] if check["status"] == "fail"}
        self.assertIn("stable_jwt_secret", failed)

    def test_remote_readiness_blocks_nonlocal_auth_disabled(self) -> None:
        config = BotBoyConfig()
        config.security.enable_auth = False
        report = build_remote_readiness_report(config, host="0.0.0.0", port=8765)
        self.assertFalse(report["ok"])
        failed = {check["name"] for check in report["checks"] if check["status"] == "fail"}
        self.assertIn("auth_required", failed)
        self.assertIn("stable_jwt_secret", failed)

    def test_remote_readiness_allows_nonlocal_with_auth_secret(self) -> None:
        config = BotBoyConfig()
        config.security.enable_auth = True
        config.security.jwt_secret = "x" * 32
        config.security.rate_limit_enabled = True
        report = build_remote_readiness_report(config, host="0.0.0.0", port=8765)
        self.assertTrue(report["ok"], report)
        self.assertTrue(report["remote"])

    def test_remote_readiness_blocks_wildcard_cors_for_nonlocal_bind(self) -> None:
        config = BotBoyConfig()
        config.security.enable_auth = True
        config.security.jwt_secret = "x" * 32
        with patch.dict(os.environ, {"BOTBOY_CORS_ALLOW_ORIGINS": "*"}):
            report = build_remote_readiness_report(config, host="0.0.0.0", port=8765)
        self.assertFalse(report["ok"])
        failed = {check["name"] for check in report["checks"] if check["status"] == "fail"}
        self.assertIn("cors_wildcard", failed)

    def test_cli_security_remote_readiness_matches_readme(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "botboy", "security", "remote-readiness", "0.0.0.0"],
            cwd=ROOT,
            env=self._env(),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("BotBoy remote readiness: FAIL", result.stdout)
        self.assertIn("Authentication must be enabled", result.stdout)
