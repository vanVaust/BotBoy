"""Redact process-wide status and dashboard telemetry from authenticated requests."""
from __future__ import annotations

from botboy.gateway.app_context import current_gateway_principal, _TaskStoreAuthorizationProxy
from botboy.__main__ import BotBoy
from botboy.dashboard_support import get_dashboard_payload as _original_dashboard_payload

_SENSITIVE_PREFIXES = (
    "  Cache hit rate:", "  Memories stored:", "  Channel messages:",
    "  Archetypes routed:", "  Trace runs:", "  Tasks tracked:",
    "  Worker nodes:", "  Reflection entries:", "  Delegation workers:",
)
_GLOBAL_TOP_LEVEL_KEYS = (
    "history", "history_stats", "trace", "trace_summary", "metrics",
    "reflection_memory", "delegation", "a2a",
)
_GLOBAL_OPERATION_KEYS = (
    "trace_runs", "latest_trace_run_id", "latest_trace_status",
    "history_total", "metrics_total_commands", "reflection_entry_count",
    "delegation_worker_count",
)

_original_show_status = BotBoy._show_status


def _scoped_show_status(self: BotBoy) -> dict:
    result = _original_show_status(self)
    if not current_gateway_principal():
        return result
    if not isinstance(result, dict) or not isinstance(result.get("output"), str):
        return result
    result = dict(result)
    result["output"] = "\n".join(
        line for line in result["output"].splitlines()
        if not line.startswith(_SENSITIVE_PREFIXES)
    )
    return result


def _scoped_dashboard_payload(bot, mode: str = "local") -> dict:
    payload = _original_dashboard_payload(bot, mode=mode)
    if not current_gateway_principal() or not isinstance(payload, dict):
        return payload
    scoped = dict(payload)
    for key in _GLOBAL_TOP_LEVEL_KEYS:
        scoped.pop(key, None)

    raw_store = getattr(bot, "task_store", None)
    if raw_store is not None:
        proxy = _TaskStoreAuthorizationProxy(raw_store, auth_enabled=True)
        try:
            scoped["tasks"] = proxy.summary()
        except Exception:
            scoped["tasks"] = {"available": True, "total": 0, "recent": [], "waiting_approval": [], "blocked": [], "delegated": []}

    operations = scoped.get("operations_summary")
    if isinstance(operations, dict):
        operations = dict(operations)
        for key in _GLOBAL_OPERATION_KEYS:
            operations.pop(key, None)
        task_summary = scoped.get("tasks")
        mapping = {
            "task_total": "total", "active_task_count": "active_count",
            "waiting_approval_count": "waiting_approval_count", "failed_task_count": "failed_count",
            "blocked_count": "blocked_count", "delegated_count": "delegated_count",
            "running_count": "running_count", "queued_count": "queued_count",
            "worker_count": "worker_count", "handoff_queue_depth": "handoff_queue_depth",
            "stale_lease_count": "stale_lease_count", "recoverable_lease_count": "recoverable_lease_count",
        }
        if isinstance(task_summary, dict):
            for target, source in mapping.items():
                if source in task_summary:
                    operations[target] = task_summary[source]
        scoped["operations_summary"] = operations
    return scoped


BotBoy._show_status = _scoped_show_status

# server.py imports get_dashboard_payload after the gateway package is initialized.
import botboy.dashboard_support as _dashboard_support
_dashboard_support.get_dashboard_payload = _scoped_dashboard_payload
