from __future__ import annotations

import unittest

from botboy.gateway.simple_runtime_write_handlers import handle_command, handle_memories_post


class _FakeMemory:
    def store(self, content: str, metadata=None) -> str:
        return "mem-1"


class _FakeBot:
    def __init__(self) -> None:
        self.memory = _FakeMemory()
        self.calls: list[dict] = []
        self.raise_error = False

    async def process_command(self, command: str, **kwargs):
        if self.raise_error:
            raise ValueError("bad command")
        self.calls.append({"command": command, **kwargs})
        return {"success": True, "output": "ok"}


class _FakeHandler:
    def __init__(self) -> None:
        self.botboy = _FakeBot()
        self.auth_enabled = False
        self.request_id = "req-1"
        self.json_payloads: list[tuple[dict, int]] = []

    def _json(self, payload: dict, status: int = 200) -> None:
        self.json_payloads.append((payload, status))

    def _ensure_access(self, require_auth: bool = False):
        return "tester"

    def _ensure_access_with_roles(self, require_auth: bool = False):
        return "tester", ["operator"]

    def _approval_context(self, payload=None, roles=None) -> dict:
        return {"granted": True}


class SimpleRuntimeWriteHandlersTest(unittest.TestCase):
    def test_handle_memories_post(self) -> None:
        handler = _FakeHandler()
        handle_memories_post(handler, {"content": "alpha"})
        payload, status = handler.json_payloads[-1]
        self.assertEqual(status, 200)
        self.assertEqual(payload["id"], "mem-1")

    def test_handle_command(self) -> None:
        handler = _FakeHandler()
        handle_command(handler, {"command": "status"})
        payload, status = handler.json_payloads[-1]
        self.assertEqual(status, 200)
        self.assertTrue(payload["success"])
        self.assertEqual(handler.botboy.calls[0]["command"], "status")

    def test_handle_command_reports_expected_runtime_error(self) -> None:
        handler = _FakeHandler()
        handler.botboy.raise_error = True
        handle_command(handler, {"command": "status"})
        payload, status = handler.json_payloads[-1]
        self.assertEqual(status, 500)
        self.assertFalse(payload["success"])
        self.assertIn("bad command", payload["output"])


if __name__ == "__main__":
    unittest.main()
