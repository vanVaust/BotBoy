"""Redact process-wide counts from authenticated status output."""
from __future__ import annotations

from botboy.gateway.app_context import current_gateway_principal

_GLOBAL_COUNTERS = (
    "Cache hit rate:", "Memories stored:", "Channel messages:",
    "Archetypes routed:", "Trace runs:", "Tasks tracked:", "Worker nodes:",
    "Reflection entries:", "Delegation workers:",
)


def _original_show_status(bot) -> dict:
    from botboy.runtime_surface_support import _build_status_response
    return _build_status_response(bot)


def _scoped_show_status(bot) -> dict:
    result = _original_show_status(bot)
    if not current_gateway_principal():
        return result
    scoped = dict(result)
    scoped["output"] = "\n".join(
        line for line in str(result.get("output", "")).splitlines()
        if not line.strip().startswith(_GLOBAL_COUNTERS)
    )
    return scoped
