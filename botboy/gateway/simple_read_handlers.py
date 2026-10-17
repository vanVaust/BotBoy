from __future__ import annotations
from botboy.gateway.simple_server_support import SimpleServerSupport


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
        **(SimpleServerSupport(handler).data_scope() if handler.auth_enabled else {}),
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
    handler._json(bot.history.stats(**(SimpleServerSupport(handler).data_scope() if handler.auth_enabled else {})))


def handle_scheduler_get(handler, params: dict) -> None:
    bot = handler.botboy
    if not (bot and bot.scheduler):
        handler._json({"error": "Scheduler not available"}, 503)
        return
    if handler._ensure_access(require_auth=handler.auth_enabled) is None:
        return
    tasks = bot.scheduler.list_tasks()
    if handler.auth_enabled:
        context = SimpleServerSupport(handler).security_context()
        if "system" not in context.roles:
            tasks = [task for task in tasks
                     if (task.payload.get("_botboy_security") or {}).get("org_id") == context.org_id
                     and ("admin" in context.roles or (task.payload.get("_botboy_security") or {}).get("principal_id") == context.principal_id)]
    handler._json({"tasks": [task.to_dict() for task in tasks], "count": len(tasks)})
