import unittest

from botboy.gateway.app_context import _current_principal
from botboy.gateway.status_read_security import _scoped_show_status


class StatusReadBoundaryTest(unittest.TestCase):
    def test_authenticated_status_redacts_process_wide_aggregates(self) -> None:
        class FakeBot:
            pass

        original = __import__(
            "botboy.gateway.status_read_security",
            fromlist=["_original_show_status"],
        )._original_show_status
        module = __import__(
            "botboy.gateway.status_read_security",
            fromlist=["_original_show_status"],
        )
        module._original_show_status = lambda _bot: {
            "success": True,
            "type": "status",
            "output": "\n".join(
                [
                    "BotBoy vtest - Status",
                    "  Memory:    OK",
                    "  Cache hit rate: 50% (1 hits / 1 misses)",
                    "  Memories stored: 9 (engine=simple)",
                    "  Channel messages: 12",
                    "  Archetypes routed: 7",
                    "  Trace runs: 5 (20 spans)",
                    "  Tasks tracked: 2 (active=1, waiting_approval=0)",
                    "  Reflection entries: 4",
                    "  Delegation workers: 5",
                    "  A2A adapters: 3",
                ]
            ),
        }
        token = _current_principal.set("tenant-user")
        try:
            result = _scoped_show_status(FakeBot())
        finally:
            _current_principal.reset(token)
            module._original_show_status = original

        self.assertIn("Memory:    OK", result["output"])
        self.assertIn("Tasks tracked: 2", result["output"])
        for forbidden in (
            "Cache hit rate:",
            "Memories stored:",
            "Channel messages:",
            "Archetypes routed:",
            "Trace runs:",
            "Reflection entries:",
            "Delegation workers:",
        ):
            self.assertNotIn(forbidden, result["output"])
        self.assertIn("A2A adapters: 3", result["output"])

    def test_local_status_is_unchanged(self) -> None:
        class FakeBot:
            pass

        module = __import__(
            "botboy.gateway.status_read_security",
            fromlist=["_original_show_status"],
        )
        original = module._original_show_status
        module._original_show_status = lambda _bot: {
            "success": True,
            "type": "status",
            "output": "BotBoy vtest - Status\n  Memories stored: 9",
        }
        try:
            result = _scoped_show_status(FakeBot())
        finally:
            module._original_show_status = original

        self.assertIn("Memories stored: 9", result["output"])
