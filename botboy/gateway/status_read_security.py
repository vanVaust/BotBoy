"""Redact process-wide status aggregates from authenticated gateway requests."""
from __future__ import annotations

from botboy.gateway.app_context import current_gateway_principal
from botboy.__main__ import BotBoy


_SENSITIVE_PREFIXES = (
    "  Cache hit rate:",
    "  Memories stored:",
    "  Channel messages:",
    "  Archetypes routed:",
    "  Trace runs:",
    "  Reflection entries:",
    "  Delegation workers:",
)


_original_show_status = BotBoy._show_status


def _scoped_show_status(self: BotBoy) -> dict:
    result = _original_show_status(self)
    if not current_gateway_principal():
        return result
    if not isinstance(result, dict) or not isinstance(result.get("output"), str):
        return result
    lines = result["output"].splitlines()
    result = dict(result)
    result["output"] = "\n".join(
        line for line in lines if not line.startswith(_SENSITIVE_PREFIXES)
    )
    return result


BotBoy._show_status = _scoped_show_status
