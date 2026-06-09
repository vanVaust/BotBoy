from __future__ import annotations

import json
import time
from typing import Any

from botboy.resources import status_snapshot_path
from botboy.runtime import available_modules


def _default_status_snapshot(bot, *, unreadable: bool = False) -> dict[str, Any]:
    trace_status: dict[str, Any] = {"available": bool(getattr(bot, "trace_store", None))}
    known_gaps: list[str] = []
    if unreadable:
        trace_status["status"] = "snapshot_unreadable"
        known_gaps.append("status_snapshot_unreadable")
    return {
        "tests": {"available": False},
        "gateway_modes": {"fastapi": "unknown", "stdlib": "verified"},
        "auth": {"available": bool(getattr(bot.config.security, "enable_auth", False))},
        "trace_status": trace_status,
        "ui_status": {
            "canonical_ui": "web/index.html",
            "control_center": "web/index.html",
            "dashboard_prototype": "web/dashboard.html",
        },
        "deferred_items": [],
        "eval_replay": {"available": False},
        "known_gaps": known_gaps,
        "last_verified_run": {},
    }


def load_status_snapshot(bot) -> dict[str, Any]:
    snapshot_path = status_snapshot_path()
    if not snapshot_path.exists():
        return _default_status_snapshot(bot)
    try:
        return json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default_status_snapshot(bot, unreadable=True)


def get_dashboard_payload(bot, mode: str = "local") -> dict[str, Any]:
    status_snapshot = dict(load_status_snapshot(bot))
    status_snapshot.setdefault("completed_capabilities", [])
    status_snapshot.setdefault("open_priorities", [])
    status_snapshot.setdefault("deferred_items", [])
    status_snapshot.setdefault("eval_replay", {"available": False})
    history_stats = bot.history.stats() if bot.history else {}
    trace_summary = bot.trace_store.summary() if bot.trace_store else {"available": False}
    task_store = getattr(bot, "task_store", None)
    task_summary = task_store.summary() if task_store else {"available": False}
    metrics_json = bot.metrics.to_json() if bot.metrics else {}
    agent_skill_library = getattr(bot, "agent_skill_library", None)
    agent_skill_stats = (
        agent_skill_library.stats()
        if agent_skill_library
        else {"count": 0, "by_category": {}, "by_phase": {}}
    )
    operator_workbench = bot.get_operator_workbench_payload(limit=12)
    operator_summary = operator_workbench.get("summary", {}) if isinstance(operator_workbench, dict) else {}
    reflection_memory = bot.get_reflection_memory_payload()
    delegation_payload = bot.get_delegation_intelligence_payload()
    a2a_payload = bot.get_a2a_pilot_payload()
    health = {
        "status": "healthy",
        "version": bot.VERSION,
        "mode": mode,
        "uptime_s": round(time.time() - bot._start_time, 1),
        "components": {
            "memory": bool(bot.memory),
            "skills": bool(bot.skills),
            "cache": bool(bot.cache),
            "scheduler": bool(bot.scheduler),
            "history": bool(bot.history),
            "trace_store": bool(bot.trace_store),
            "task_store": bool(task_store),
            "llm": bool(bot.llm),
            "router": bool(bot.router),
            "archetypes": bool(bot.archetypes),
            "reflection_memory": bool(reflection_memory.get("available")),
            "delegation": bool(delegation_payload.get("available")),
            "a2a_pilot": bool(a2a_payload.get("available")),
        },
    }
    readiness = {
        "core": "verified",
        "gateway": "verified",
        "auth": "verified" if getattr(bot.config.security, "enable_auth", False) else "available",
        "trace": "verified" if bot.trace_store else "offline",
        "mcp": "available",
        "ui": "verified",
        "voice": "experimental",
        "fastapi": "available" if available_modules().get("fastapi") else "offline",
    }
    verified_signals = sum(1 for value in readiness.values() if value == "verified")
    attention_signals = sorted(
        key for key, value in readiness.items() if value not in {"verified", "available"}
    )
    suite_result = status_snapshot.get("verified_test_result", {})
    suite_status = str(suite_result.get("status", "")).upper()
    suite_ok = suite_status == "OK"
    if not suite_ok:
        overall_status = "attention"
    elif attention_signals:
        overall_status = "watch"
    else:
        overall_status = "verified"
    operations_summary = {
        "overall_status": overall_status,
        "verified_signals": verified_signals,
        "attention_signals": attention_signals,
        "suite_status": suite_status or "UNKNOWN",
        "suite_ran": int(suite_result.get("ran", 0) or 0),
        "suite_skipped": int(suite_result.get("skipped", 0) or 0),
        "known_gap_count": len(status_snapshot.get("known_gaps", [])),
        "open_priority_count": len(status_snapshot.get("open_priorities", [])),
        "completed_capability_count": len(status_snapshot.get("completed_capabilities", [])),
        "current_wave": status_snapshot.get("current_wave", ""),
        "gateway_modes": status_snapshot.get("gateway_modes", {}),
        "trace_runs": int(trace_summary.get("total_runs", 0) or 0),
        "latest_trace_run_id": trace_summary.get("latest_run_id", ""),
        "latest_trace_status": trace_summary.get("latest_status", ""),
        "history_total": int(history_stats.get("total", 0) or 0),
        "metrics_total_commands": int(metrics_json.get("total_commands", 0) or 0),
        "task_total": int(task_summary.get("total", 0) or 0),
        "active_task_count": int(task_summary.get("active_count", 0) or 0),
        "waiting_approval_count": int(task_summary.get("waiting_approval_count", 0) or 0),
        "failed_task_count": int(task_summary.get("failed_count", 0) or 0),
        "blocked_count": int(task_summary.get("blocked_count", 0) or 0),
        "delegated_count": int(task_summary.get("delegated_count", 0) or 0),
        "running_count": int(task_summary.get("running_count", 0) or 0),
        "queued_count": int(task_summary.get("queued_count", 0) or 0),
        "worker_count": int(task_summary.get("worker_count", 0) or 0),
        "handoff_queue_depth": int(task_summary.get("handoff_queue_depth", 0) or 0),
        "stale_lease_count": int(task_summary.get("stale_lease_count", 0) or 0),
        "recoverable_lease_count": int(task_summary.get("recoverable_lease_count", 0) or 0),
        "merge_review_ready_count": int(task_summary.get("merge_review_ready_count", 0) or 0),
        "merge_conflict_task_count": int(task_summary.get("merge_conflict_task_count", 0) or 0),
        "merge_conflict_total": int(task_summary.get("merge_conflict_total", 0) or 0),
        "merge_actionable_count": int(task_summary.get("merge_actionable_count", 0) or 0),
        "merge_override_task_count": int(task_summary.get("merge_override_task_count", 0) or 0),
        "merge_override_total": int(task_summary.get("merge_override_total", 0) or 0),
        "merge_resolution_policies": task_summary.get("merge_resolution_policies", {}),
        "latest_merge_task_id": task_summary.get("latest_merge_task_id", ""),
        "latest_merge_resolution_policy": task_summary.get("latest_merge_resolution_policy", ""),
        "latest_task_id": task_summary.get("latest_task_id", ""),
        "merge_queue_total": int(operator_summary.get("merge_total", 0) or 0),
        "operator_actionable_count": (
            int(operator_summary.get("merge_actionable_count", 0) or 0)
            + int(operator_summary.get("recoverable_lease_count", 0) or 0)
            + int(operator_summary.get("blocked_count", 0) or 0)
        ),
        "reflection_entry_count": int(reflection_memory.get("total_entries", 0) or 0),
        "delegation_worker_count": int(delegation_payload.get("worker_count", 0) or 0),
        "a2a_adapter_count": int(a2a_payload.get("total_adapters", 0) or 0),
        "agent_skill_count": int(agent_skill_stats.get("count", 0) or 0),
    }
    return {
        "health": health,
        "system_readiness": readiness,
        "operations_summary": operations_summary,
        "metrics": metrics_json,
        "monitoring": bot.get_monitoring_payload(),
        "history": history_stats,
        "traces": trace_summary,
        "tasks": task_summary,
        "operator_workbench": operator_workbench,
        "merge_review_queue": (
            operator_workbench.get("merge_queue", {})
            if isinstance(operator_workbench, dict)
            else {}
        ),
        "reflection_memory": reflection_memory,
        "delegation": delegation_payload,
        "a2a_pilot": a2a_payload,
        "workers": bot.get_worker_payload(),
        "agent_skill_library": {
            "available": bool(agent_skill_library),
            "count": int(agent_skill_stats.get("count", 0) or 0),
            "by_category": agent_skill_stats.get("by_category", {}),
            "by_phase": agent_skill_stats.get("by_phase", {}),
        },
        "status_snapshot": status_snapshot,
        "known_gaps": status_snapshot.get("known_gaps", []),
    }
