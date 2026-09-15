import inspect
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
            import fastapi.dependencies.utils as dependency_utils

            original = dependency_utils.get_typed_signature
            candidates = []

            def debug_signature(call):
                annotations = getattr(call, "__annotations__", {}) or {}
                if any(name == "request" and (value == "Request" or "Request" in repr(value)) for name, value in annotations.items()):
                    candidates.append({
                        "endpoint": getattr(call, "__qualname__", repr(call)),
                        "module": getattr(call, "__module__", None),
                        "annotations": repr(annotations),
                        "signature": repr(inspect.signature(call, eval_str=False)),
                    })
                return original(call)

            with patch.object(dependency_utils, "get_typed_signature", side_effect=debug_signature):
                try:
                    app.openapi()
                except Exception as exc:
                    self.fail(f"OpenAPI failed; Request candidates={candidates!r}; exception={exc!r}")
        finally:
            bot.shutdown()
