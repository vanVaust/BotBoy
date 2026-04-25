from __future__ import annotations

import json
import secrets

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from botboy.gateway.app_context import GatewayAppContext


async def _send_ws_json_safe(websocket: WebSocket, payload: dict) -> bool:
    try:
        await websocket.send_json(payload)
        return True
    except (RuntimeError, OSError, WebSocketDisconnect):
        return False


async def _close_ws_safe(websocket: WebSocket, *, code: int) -> None:
    try:
        await websocket.close(code=code)
    except (RuntimeError, OSError, WebSocketDisconnect):
        return


def create_ws_router(ctx: GatewayAppContext) -> APIRouter:
    router = APIRouter()
    bot = ctx.bot

    @router.websocket("/ws/chat")
    async def websocket_chat(websocket: WebSocket):
        token_info = None
        token = ctx.auth.extract_bearer_token(
            websocket.headers.get("authorization") or websocket.headers.get("Authorization") or ""
        )
        request_id = websocket.headers.get("x-request-id") or websocket.headers.get("X-Request-ID") or f"req-{secrets.token_hex(8)}"
        websocket.state.request_id = request_id
        if token:
            token_info = ctx.auth.verify(token)
        if ctx.auth_enabled and not token_info:
            await _close_ws_safe(websocket, code=1008)
            return
        await websocket.accept()
        websocket_identity = token_info.principal_id if token_info else (
            websocket.headers.get("x-forwarded-for")
            or websocket.headers.get("x-real-ip")
            or (websocket.client.host if websocket.client else "unknown")
        )
        try:
            while True:
                raw = await websocket.receive_text()
                msg = {}
                command = raw.strip()
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    payload = None
                if isinstance(payload, dict):
                    msg = payload
                    command = str(payload.get("command", "")).strip()

                if not command:
                    if not await _send_ws_json_safe(websocket, {"type": "error", "output": "Empty command"}):
                        return
                    continue

                if ctx.rate_limiter:
                    result = ctx.rate_limiter.check(websocket_identity)
                    if not result.allowed:
                        if not await _send_ws_json_safe(websocket, {"type": "error", "output": "Rate limit exceeded"}):
                            return
                        continue

                if bot.llm and command.startswith("chat "):
                    query = command[5:].strip()
                    if not await _send_ws_json_safe(websocket, {"type": "stream_start"}):
                        return
                    full_response = ""
                    async for chunk in bot.llm.stream(query):
                        full_response += chunk
                        if not await _send_ws_json_safe(websocket, {"type": "chunk", "content": chunk}):
                            return
                    if not await _send_ws_json_safe(
                        websocket,
                        {"type": "stream_end", "output": full_response, "success": True},
                    ):
                        return
                else:
                    result = await bot.process_command(
                        command,
                        principal=websocket_identity,
                        request_id=getattr(websocket.state, "request_id", ""),
                        roles=list(token_info.roles) if token_info else [],
                        approval_context=ctx.approval_context(
                            websocket.headers,
                            msg if isinstance(msg, dict) else {},
                            list(token_info.roles) if token_info else [],
                        ),
                    )
                    if not await _send_ws_json_safe(websocket, {**result, "type": result.get("type", "result")}):
                        return
        except WebSocketDisconnect:
            return
        except (RuntimeError, ValueError) as exc:
            await _send_ws_json_safe(websocket, {"type": "error", "output": str(exc)})
            await _close_ws_safe(websocket, code=1011)

    return router
