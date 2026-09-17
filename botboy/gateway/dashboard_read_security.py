"""Prevent authenticated dashboard reads from exposing process-wide telemetry."""
from __future__ import annotations

from botboy.gateway.app_context import current_gateway_principal
from botboy.dashboard_support import get_dashboard_payload as _original_dashboard_payload


_GLOBAL_TOP_LEVEL_KEYS = (
    "history",
    "history_stats",
    "trace",
    "trace_summary",
    "metrics",
    "reflection_memory",
    "delegation",
    "a2a",
)

_GLOBAL_OPERATION_KEYS = (
    "trace_runs",
    "latest_trace_run_id",
    "latest_trace_status",
    "history_total",
    "metrics_total_commands",
    "reflection_entry_count",
    "delegation_worker_count",
)


def _scoped_dashboard_payload(bot, mode: str = "local") -> dict:
    payload = _original_dashboard_payload(bot, mode=mode)
    if not current_gateway_principal() or not isinstance(payload, dict):
        return payload

    scoped = dict(payload)
    for key in _GLOBAL_TOP_LEVEL_KEYS:
        scoped.pop(key, None)

    operations = scoped.get("operations_summary")
    if isinstance(operations, dict):
        operations = dict(operations)
        for key in _GLOBAL_OPERATION_KEYS:
            operations.pop(key, None)
        scoped["operations_summary"] = operations

    return scoped


import botboy.dashboard_support as _dashboard_support
_dashboard_support.get_dashboard_payload = _scoped_dashboard_payload
