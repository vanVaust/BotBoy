from __future__ import annotations

import sqlite3


def handle_history_get(handler, params: dict) -> None:
    bot = handler.botboy
    if not (bot and bot.history):
        handler._json({"error": "History not available"}, 503)
        return
    if handler._ensure_access(require_auth=handler.auth_enabled) is None:
        return
    limit = int(params.get("limit", ["20"])[0])
    offset = int(params.get("offset", ["0"])[0])
    search = params.get("search", [None])[0]
    request_id = params.get("request_id", [None])[0]
    records, total = bot.history.list(
        limit=min(limit, 100),
        offset=offset,
        search=search,
        request_id=request_id,
    )
    handler._json(
        {
            "records": [record.to_dict() for record in records],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    )


def handle_history_stats(handler, params: dict) -> None:
    bot = handler.botboy
    if not (bot and bot.history):
        handler._json({"error": "History not available"}, 503)
        return
    if handler._ensure_access(require_auth=handler.auth_enabled) is None:
        return
    handler._json(bot.history.stats())


def handle_scheduler_get(handler, params: dict) -> None:
    bot = handler.botboy
    if not (bot and bot.scheduler):
        handler._json({"error": "Scheduler not available"}, 503)
        return
    if handler._ensure_access(require_auth=handler.auth_enabled) is None:
        return
    tasks = bot.scheduler.list_tasks()
    stats = bot.scheduler.stats() if hasattr(bot.scheduler, "stats") else {"count": len(tasks)}
    handler._json({"tasks": [task.to_dict() for task in tasks], "stats": stats, "count": len(tasks)})


def handle_scheduler_post(handler, payload: dict) -> None:
    bot = handler.botboy
    if not (bot and bot.scheduler):
        handler._json({"error": "Scheduler not available"}, 503)
        return
    if handler._ensure_access(require_auth=handler.auth_enabled) is None:
        return
    name = str(payload.get("name", "")).strip()
    schedule = str(payload.get("schedule", "")).strip()
    if not name or not schedule:
        handler._json({"error": "Missing 'name' or 'schedule'"}, 400)
        return
    try:
        task_id = bot.scheduler.add(
            name=name,
            schedule=schedule,
            task_type=payload.get("task_type", "generic"),
            payload=payload.get("payload"),
        )
    except (OSError, RuntimeError, TypeError, ValueError, sqlite3.Error) as exc:
        handler._json({"error": str(exc)}, 400)
        return
    handler._json({"task_id": task_id, "name": name, "schedule": schedule})


def handle_scheduler_delete(handler, task_id: str) -> None:
    bot = handler.botboy
    if not (bot and bot.scheduler):
        handler._json({"error": "Scheduler not available"}, 503)
        return
    if handler._ensure_access(require_auth=handler.auth_enabled) is None:
        return
    bot.scheduler.cancel(task_id)
    handler._json({"cancelled": task_id})
