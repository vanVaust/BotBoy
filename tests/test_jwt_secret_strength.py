from __future__ import annotations

import os
import secrets
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from botboy.core.config import BotBoyConfig
from botboy.gateway.auth import JWTAuth
from botboy.gateway.security import build_remote_readiness_report, secret_strength_problem


ROOT = Path(__file__).resolve().parents[1]
PUBLISHED_SECRET = "change_me_use_at_least_32_random_characters_here"


class JwtSecretStrengthTests(unittest.TestCase):
    def test_public_placeholder_and_weak_repeated_secrets_are_rejected(self) -> None:
        for weak in (
            PUBLISHED_SECRET,
            "replace-me-with-a-real-secret-0123456789",
            "x" * 32,
            "abababababababababababababababab",
        ):
            with self.subTest(secret_type=weak[:15]):
                self.assertTrue(secret_strength_problem(weak))
                with self.assertRaises(ValueError):
                    JWTAuth(weak)

    def test_short_secret_is_rejected_by_readiness_too(self) -> None:
        self.assertIn("32", secret_strength_problem("short-but-varied-0123456789"))

    def test_random_secret_is_accepted_and_tokens_verify(self) -> None:
        secret = secrets.token_hex(32)
        self.assertFalse(secret_strength_problem(secret))
        auth = JWTAuth(secret)
        pair = auth.create_pair("alice", roles=["user"])
        self.assertEqual(auth.verify(pair.access_token).principal_id, "alice")

    def test_remote_readiness_rejects_public_placeholder(self) -> None:
        config = SimpleNamespace(security=SimpleNamespace(
            enable_auth=True, rate_limit_enabled=True, jwt_secret=PUBLISHED_SECRET,
        ))
        with patch.dict(os.environ, {"BOTBOY_CORS_ALLOW_ORIGINS": ""}):
            report = build_remote_readiness_report(config, host="0.0.0.0")
            self.assertFalse(report["ok"])
            config.security.jwt_secret = secrets.token_hex(32)
            self.assertTrue(build_remote_readiness_report(config, host="0.0.0.0")["ok"])

    def test_config_and_env_example_do_not_consider_placeholder_configured(self) -> None:
        config = BotBoyConfig()
        config.security.jwt_secret = PUBLISHED_SECRET
        self.assertFalse(config.has_jwt_secret())
        content = (ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertNotIn(PUBLISHED_SECRET, content)
        self.assertIn("BOTBOY_JWT_SECRET=\n", content)
        self.assertIn("NOT auto-persisted", content)


if __name__ == "__main__":
    unittest.main()
