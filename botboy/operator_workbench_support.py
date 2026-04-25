from __future__ import annotations

from typing import Any


def merge_review_signal_fields(bot, merge: dict[str, Any]) -> dict[str, Any]:
    configured_policy = str(merge.get("configured_resolution_policy", "") or "").strip()
    effective_policy = str(
        merge.get("resolution_policy")
        or (merge.get("merge_resolution", {}) or {}).get("policy")
        or ""
    ).strip()
    review_pending_keys = [
        str(item).strip()
        for item in merge.get("review_pending_keys", [])
        if str(item).strip()
    ]
    pending_child_ids = [
        str(item).strip()
        for item in merge.get("pending_child_ids", [])
        if str(item).strip()
    ]
    override_count = int(merge.get("override_count", 0) or 0)
    actionable = bool(review_pending_keys or pending_child_ids)
    override_active = override_count > 0
    policy_delta = (
        f"{configured_policy} -> {effective_policy}"
        if configured_policy and effective_policy and configured_policy != effective_policy
        else "aligned"
    )
    review_state = str(merge.get("review_status", "") or "").strip() or (
        "needs_attention" if actionable else "clean"
    )
    next_action = bot._merge_review_next_action(
        pending_child_ids=pending_child_ids,
        review_pending_keys=review_pending_keys,
        override_count=override_count,
    )
    return {
        "configured_resolution_policy": configured_policy,
        "effective_resolution_policy": effective_policy,
        "actionable": actionable,
        "override_active": override_active,
        "policy_delta": policy_delta,
        "review_state": review_state,
        "next_action": next_action,
        "available_review_presets": bot._merge_available_review_presets(merge),
        "known_review_keys": bot._merge_known_review_keys(merge),
        "valid_sources": bot._merge_valid_sources(merge),
    }


def summarize_merge_queue_item(bot, record, merge: dict[str, Any]) -> dict[str, Any]:
    signals = merge_review_signal_fields(bot, merge)
    merge_resolution = (
        merge.get("merge_resolution", {})
        if isinstance(merge.get("merge_resolution", {}), dict)
        else {}
    )
    queue_priority = (
        len(signals["known_review_keys"]) * 10
        + int(merge_resolution.get("conflict_count", 0) or 0) * 5
        + len(merge.get("pending_child_ids", [])) * 20
        + (25 if signals["actionable"] else 0)
        + (10 if signals["override_active"] else 0)
    )
    return {
        "task_id": record.task_id,
        "root_task_id": record.root_task_id,
        "status": record.status,
        "owner": record.owner,
        "title": record.title,
        "updated_at": record.updated_at,
        "merge_policy": merge.get("merge_policy", ""),
        "configured_resolution_policy": signals["configured_resolution_policy"],
        "resolution_policy": signals["effective_resolution_policy"],
        "review_status": signals["review_state"],
        "review_pending_keys": list(merge.get("review_pending_keys", [])),
        "applied_override_keys": list(merge.get("applied_override_keys", [])),
        "pending_child_ids": list(merge.get("pending_child_ids", [])),
        "workers_involved": list(merge.get("workers_involved", [])),
        "conflict_count": int(merge_resolution.get("conflict_count", 0) or 0),
        "override_count": int(merge.get("override_count", 0) or 0),
        "actionable": bool(signals["actionable"]),
        "override_active": bool(signals["override_active"]),
        "policy_delta": signals["policy_delta"],
        "next_action": signals["next_action"],
        "available_review_presets": signals["available_review_presets"],
        "queue_priority": queue_priority,
    }


def get_merge_review_queue(
    bot,
    *,
    limit: int = 20,
    include_non_actionable: bool = False,
) -> dict[str, Any]:
    if not bot.task_store:
        return {"available": False, "items": [], "total": 0}
    list_tasks = getattr(bot.task_store, "list_tasks", None)
    if not callable(list_tasks):
        return {"available": True, "items": [], "total": 0, "actionable_count": 0}
    records, _total = list_tasks(limit=max(limit * 10, 100))
    items: list[dict[str, Any]] = []
    for record in records:
        merge = bot.get_task_merge_payload(record.task_id, record=record)
        if not merge.get("available"):
            continue
        item = summarize_merge_queue_item(bot, record, merge)
        if not include_non_actionable and not item["actionable"]:
            continue
        items.append(item)
    items.sort(
        key=lambda item: (
            -int(item.get("actionable", False)),
            -int(item.get("conflict_count", 0)),
            -int(item.get("override_count", 0)),
            -int(item.get("queue_priority", 0)),
            str(item.get("updated_at", "")),
        )
    )
    limited = items[:limit]
    return {
        "available": True,
        "items": limited,
        "total": len(items),
        "actionable_count": sum(1 for item in items if item.get("actionable")),
    }


def get_operator_workbench_payload(bot, *, limit: int = 10) -> dict[str, Any]:
    if not bot.task_store:
        return {"available": False}
    summary_method = getattr(bot.task_store, "summary", None)
    task_summary = summary_method() if callable(summary_method) else {}
    merge_queue = get_merge_review_queue(bot, limit=limit)
    recoverable_leases = list(task_summary.get("recoverable_leases", []))[:limit]
    stale_leases = list(task_summary.get("stale_leases", []))[:limit]
    blocked = list(task_summary.get("blocked", []))[:limit]
    return {
        "available": True,
        "merge_queue": merge_queue,
        "blockers": blocked,
        "recoverable_leases": recoverable_leases,
        "stale_leases": stale_leases,
        "summary": {
            "merge_actionable_count": int(merge_queue.get("actionable_count", 0) or 0),
            "merge_total": int(merge_queue.get("total", 0) or 0),
            "blocked_count": int(task_summary.get("blocked_count", 0) or 0),
            "recoverable_lease_count": int(task_summary.get("recoverable_lease_count", 0) or 0),
            "stale_lease_count": int(task_summary.get("stale_lease_count", 0) or 0),
        },
    }
