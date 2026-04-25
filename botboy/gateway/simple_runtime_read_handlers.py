from __future__ import annotations

import asyncio
from typing import Any


def _run_sync(coro) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, coro)
        return future.result(timeout=30)


def handle_health(handler, params: dict) -> None:
    bot = handler.botboy
    response = {
        "status": "healthy",
        "version": getattr(bot, "VERSION", "0.6.0-dev"),
        "mode": "stdlib",
        "components": {
            "memory": bool(getattr(bot, "memory", None)),
            "skills": bool(getattr(bot, "skills", None)),
            "cache": bool(getattr(bot, "cache", None)),
            "scheduler": bool(getattr(bot, "scheduler", None)),
            "history": bool(getattr(bot, "history", None)),
            "trace_store": bool(getattr(bot, "trace_store", None)),
            "llm": bool(getattr(bot, "llm", None)),
        },
        "security": {
            "enable_auth": bool(handler.auth_enabled),
            "rate_limit_enabled": bool(handler.rate_limiter),
            "auth_api_keys_enabled": bool(getattr(handler.security, "auth_api_keys_enabled", True)),
        },
    }
    if bot and bot.cache:
        cache_stats = bot.cache.stats()
        response["cache"] = {"hit_rate": round(cache_stats.hit_rate, 3), "size": cache_stats.size}
    handler._json(response)


def handle_metrics_text(handler, params: dict) -> None:
    bot = handler.botboy
    if bot and bot.metrics:
        body, content_type = bot.metrics.render()
        handler._text(body, content_type)
    else:
        handler._text("# no metrics\n")


def handle_status(handler, params: dict) -> None:
    bot = handler.botboy
    if not bot:
        handler._json({"error": "Bot not initialized"}, 503)
        return
    principal_id, roles = handler._ensure_access_with_roles(require_auth=handler.auth_enabled)
    if principal_id is None:
        return
    result = _run_sync(
        bot.process_command(
            "status",
            principal=principal_id,
            request_id=handler.request_id,
            roles=roles,
            approval_context=handler._approval_context({}, roles),
        )
    )
    handler._json(result)


def handle_skills(handler, params: dict) -> None:
    bot = handler.botboy
    skills = bot.skills.list_skills() if (bot and bot.skills) else []
    handler._json({"skills": skills, "count": len(skills)})


def handle_memories_get(handler, params: dict) -> None:
    bot = handler.botboy
    if not (bot and bot.memory):
        handler._json({"error": "Memory not available"}, 503)
        return
    if handler._ensure_access(require_auth=handler.auth_enabled) is None:
        return
    limit = int(params.get("limit", ["20"])[0])
    offset = int(params.get("offset", ["0"])[0])
    memories = bot.memory.list_all(limit=min(limit, 100), offset=offset)
    stats = bot.memory.get_stats()
    handler._json(
        {
            "memories": [memory.to_dict() for memory in memories],
            "total": stats["total"],
            "limit": limit,
            "offset": offset,
        }
    )


def handle_memories_search(handler, params: dict) -> None:
    bot = handler.botboy
    if not (bot and bot.memory):
        handler._json({"error": "Memory not available"}, 503)
        return
    if handler._ensure_access(require_auth=handler.auth_enabled) is None:
        return
    query = params.get("q", [""])[0]
    limit = int(params.get("limit", ["10"])[0])
    if not query:
        handler._json({"error": "Missing q parameter"}, 400)
        return
    results = bot.memory.search(query, limit=min(limit, 50))
    handler._json({"results": [result.to_dict() for result in results], "query": query, "count": len(results)})


def handle_metrics_json(handler, params: dict) -> None:
    bot = handler.botboy
    if bot and bot.metrics:
        handler._json(bot.metrics.to_json())
    else:
        handler._json({"error": "Metrics not available"}, 503)


def handle_monitoring_json(handler, params: dict) -> None:
    bot = handler.botboy
    if not bot:
        handler._json({"error": "Bot not initialized"}, 503)
        return
    if handler._ensure_access(require_auth=handler.auth_enabled) is None:
        return
    payload = bot.get_monitoring_payload()
    store = handler._task_store(optional=True)
    if store:
        payload.setdefault("tasks", {})
        payload["tasks"].update(handler._task_metrics(store))
        payload["workers"] = handler._task_workers_payload(store)
    handler._json(payload)


def handle_dashboard_json(handler, params: dict) -> None:
    bot = handler.botboy
    if not bot:
        handler._json({"error": "Bot not initialized"}, 503)
        return
    if handler._ensure_access(require_auth=handler.auth_enabled) is None:
        return
    handler._json(handler._dashboard_payload("stdlib"))
