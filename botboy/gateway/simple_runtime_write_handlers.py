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


def handle_memories_post(handler, payload: dict) -> None:
    bot = handler.botboy
    if not (bot and bot.memory):
        handler._json({"error": "Memory not available"}, 503)
        return
    if handler._ensure_access(require_auth=handler.auth_enabled) is None:
        return
    content = str(payload.get("content", "")).strip()
    if not content:
        handler._json({"error": "Missing content"}, 400)
        return
    mem_id = bot.memory.store(content, payload.get("metadata"))
    handler._json({"id": mem_id, "content": content, "success": True})


def handle_command(handler, payload: dict) -> None:
    bot = handler.botboy
    if not bot:
        handler._json({"error": "Bot not initialized"}, 503)
        return
    principal_id, roles = handler._ensure_access_with_roles(require_auth=handler.auth_enabled)
    if principal_id is None:
        return
    command = str(payload.get("command", "")).strip()
    if not command:
        handler._json({"error": "Missing command field"}, 400)
        return
    try:
        result = _run_sync(
            bot.process_command(
                command,
                principal=principal_id,
                request_id=handler.request_id,
                roles=roles,
                approval_context=handler._approval_context(payload, roles),
            )
        )
        handler._json(result)
    except (ValueError, RuntimeError, PermissionError, LookupError) as exc:
        handler._json({"success": False, "output": str(exc), "type": "error"}, 500)
