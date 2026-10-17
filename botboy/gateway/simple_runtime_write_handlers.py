from __future__ import annotations

import asyncio
from contextvars import copy_context
from typing import Any
from botboy.gateway.simple_server_support import SimpleServerSupport


def _run_sync(coro) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(copy_context().run, asyncio.run, coro)
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
    metadata = dict(payload.get("metadata") or {}) if isinstance(payload.get("metadata"), dict) else {}
    if handler.auth_enabled:
        context = SimpleServerSupport(handler).security_context()
        metadata["_botboy_security"] = {"principal_id": context.principal_id, "org_id": context.org_id}
    mem_id = bot.memory.store(content, metadata)
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
                **({"security_context": SimpleServerSupport(handler).security_context()} if handler.auth_enabled else {}),
            )
        )
        handler._json(result)
    except (ValueError, RuntimeError, PermissionError, LookupError) as exc:
        handler._json({"success": False, "output": str(exc), "type": "error"}, 500)
