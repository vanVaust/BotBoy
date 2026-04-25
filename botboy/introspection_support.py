from __future__ import annotations

from typing import Any

from botboy.workers import WorkerRegistry


def get_monitoring_payload(bot) -> dict[str, Any]:
    if not bot.monitor:
        return {"available": False, "summary": None, "metrics": []}
    summary = bot.monitor.get_observability_summary()
    metrics = []
    for name, stats in sorted(bot.monitor.get_all_stats().items()):
        metrics.append(
            {
                "name": name,
                "count": stats.count,
                "min_ms": stats.min_ms,
                "max_ms": stats.max_ms,
                "avg_ms": stats.avg_ms,
                "p50_ms": stats.p50_ms,
                "p95_ms": stats.p95_ms,
                "p99_ms": stats.p99_ms,
            }
        )
    payload = {"available": True, "summary": summary, "metrics": metrics[:50]}
    task_store = getattr(bot, "task_store", None)
    if task_store:
        payload["tasks"] = task_store.summary()
    return payload


def get_worker_payload(bot) -> dict[str, Any]:
    registry = getattr(bot, "workers", None) or WorkerRegistry()
    workers = [worker.to_dict() for worker in registry.list_workers()]
    task_store = getattr(bot, "task_store", None)
    summary = (
        task_store.worker_summary()
        if task_store
        else {"by_worker": {}, "active_by_worker": {}, "worker_count": 0}
    )
    for worker in workers:
        worker_id = worker["worker_id"]
        worker["active_tasks"] = int(summary.get("active_by_worker", {}).get(worker_id, 0) or 0)
        worker["delegated_tasks"] = int(summary.get("by_worker", {}).get(worker_id, 0) or 0)
    return {
        "available": True,
        "workers": workers,
        "worker_count": len(workers),
    }


def _empty_reflection_memory_payload() -> dict[str, Any]:
    return {
        "available": False,
        "total_entries": 0,
        "by_outcome": {},
        "top_tags": {},
        "latest_entry": None,
    }


def get_reflection_memory_payload(bot) -> dict[str, Any]:
    archive = getattr(bot, "reflection_archive", None)
    if not archive:
        return _empty_reflection_memory_payload()
    try:
        return archive.stats()
    except Exception:
        return _empty_reflection_memory_payload()


def get_delegation_intelligence_payload(bot) -> dict[str, Any]:
    advisor = getattr(bot, "delegation_advisor", None)
    if not advisor:
        return {"available": False, "worker_count": 0, "workers": []}
    workers = advisor.list_workers()
    by_role: dict[str, int] = {}
    for worker in workers:
        role = str(worker.get("role", "unknown") or "unknown")
        by_role[role] = by_role.get(role, 0) + 1
    return {
        "available": True,
        "worker_count": len(workers),
        "workers": workers,
        "by_role": dict(sorted(by_role.items())),
    }


def _empty_a2a_pilot_payload() -> dict[str, Any]:
    return {"available": False, "total_adapters": 0, "capabilities": {}, "adapters": []}


def get_a2a_pilot_payload(bot) -> dict[str, Any]:
    registry = getattr(bot, "a2a_pilot", None)
    if not registry:
        return _empty_a2a_pilot_payload()
    try:
        return registry.stats()
    except Exception:
        return _empty_a2a_pilot_payload()
