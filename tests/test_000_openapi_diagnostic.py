import unittest
from pathlib import Path
import sys
from types import SimpleNamespace

from botboy.__main__ import BotBoy
from botboy.core.config import BotBoyConfig
from botboy.gateway.server import create_app
from fastapi.routing import APIRoute
from typing import ForwardRef

ROOT = Path(__file__).resolve().parents[1]


class OpenAPIDiagnosticTest(unittest.TestCase):
    def test_no_unresolved_request_forward_refs(self):
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
            offenders = []
            for route in app.routes:
                if not isinstance(route, APIRoute):
                    continue
                fields = (
                    list(route.dependant.path_params)
                    + list(route.dependant.query_params)
                    + list(route.dependant.header_params)
                    + list(route.dependant.cookie_params)
                    + list(route.dependant.body_params)
                )
                for field in fields:
                    annotation = getattr(field, "type_", None)
                    if isinstance(annotation, ForwardRef) and annotation.__forward_arg__ == "Request":
                        offenders.append((route.path, route.name, field.name, repr(annotation)))
            self.assertFalse(offenders, offenders)
        finally:
            bot.shutdown()
