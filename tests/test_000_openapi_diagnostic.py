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
            import fastapi.routing as routing

            original = routing._build_dependant_with_parameterless_dependencies
            offenders = []

            def debug_build(*, path, call, dependencies):
                dependant = original(path=path, call=call, dependencies=dependencies)
                fields = list(dependant.path_params) + list(dependant.query_params) + list(dependant.header_params) + list(dependant.cookie_params) + list(dependant.body_params)
                for field in fields:
                    if getattr(field, "name", None) == "request":
                        offenders.append({
                            "path": path,
                            "endpoint": getattr(call, "__qualname__", repr(call)),
                            "module": getattr(call, "__module__", None),
                            "annotations": repr(getattr(call, "__annotations__", {})),
                            "signature": repr(inspect.signature(call, eval_str=False)),
                            "type_": repr(getattr(field, "type_", None)),
                            "annotation": repr(getattr(field, "annotation", None)),
                            "field_info_annotation": repr(getattr(getattr(field, "field_info", None), "annotation", None)),
                        })
                return dependant

            with patch.object(routing, "_build_dependant_with_parameterless_dependencies", side_effect=debug_build):
                try:
                    app.openapi()
                except Exception as exc:
                    self.fail(f"OpenAPI failed; request fields={offenders!r}; exception={exc!r}")
        finally:
            bot.shutdown()
