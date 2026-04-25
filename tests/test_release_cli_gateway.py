from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

from botboy.gateway.security import default_bind_host, resolve_cors_origins


ROOT = Path(__file__).resolve().parents[1]


class ReleaseCliGatewayTest(unittest.TestCase):
    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["BOTBOY_HOME"] = str((ROOT / ".botboy-runtime" / "cli-gateway-tests").resolve())
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
        origins = resolve_cors_origins()
        self.assertTrue(origins)
        self.assertTrue(all("localhost" in origin or "127.0.0.1" in origin for origin in origins))
        self.assertFalse(any("0.0.0.0" in origin for origin in origins))
