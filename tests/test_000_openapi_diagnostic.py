import unittest
from pathlib import Path
from unittest.mock import patch

from botboy.__main__ import BotBoy
from botboy.core.config import BotBoyConfig
from botboy.gateway.server import create_app

ROOT = Path(__file__).resolve().parents[1]


class OpenAPIDiagnosticTest(unittest.TestCase):
    def test_locate_request_forward_ref_during_openapi_generation(self):
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
        try:
            app = create_app(bot, host="127.0.0.1", port=8765)
            import fastapi.openapi.utils as openapi_utils

            original = openapi_utils.get_definitions
            offenders = []

            def debug_get_definitions(*, fields, **kwargs):
                for field in fields:
                    text = repr(field)
                    if "Request" in text or "ForwardRef" in text:
                        offenders.append((getattr(field, "name", ""), text))
                return original(fields=fields, **kwargs)

            with patch.object(openapi_utils, "get_definitions", side_effect=debug_get_definitions):
                try:
                    app.openapi()
                except Exception as exc:
                    self.fail(f"OpenAPI failed; candidate fields={offenders!r}; exception={exc!r}")
        finally:
            bot.shutdown()
