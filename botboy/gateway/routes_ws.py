from __future__ import annotations

import json
import secrets

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from botboy.gateway.app_context import GatewayAppContext, _current_principal, _current_org, _current_roles


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

                # Revalidate each message, including expiry, revocation and
                # changed credentials. Streaming must not bypass execution.
                tokens = (_current_principal.set(""), _current_org.set("default"), _current_roles.set(()))
                try:
                    principal, roles = ctx.authorize_with_roles(
                        websocket.headers, websocket.client.host if websocket.client else "",
                        require_auth=ctx.auth_enabled,
                    )
                    result = await bot.process_command(
                        command,
                        principal=principal,
                        request_id=getattr(websocket.state, "request_id", ""),
                        roles=roles,
                        approval_context=ctx.approval_context(
                            websocket.headers,
                            msg if isinstance(msg, dict) else {},
                            roles,
                        ),
                        **({"security_context": ctx.security_context(request_id=request_id)} if ctx.auth_enabled else {}),
                    )
                    if not await _send_ws_json_safe(websocket, {**result, "type": result.get("type", "result")}):
                        return
                except (HTTPException, PermissionError):
                    await _close_ws_safe(websocket, code=1008)
                    return
                finally:
                    _current_principal.reset(tokens[0])
                    _current_org.reset(tokens[1])
                    _current_roles.reset(tokens[2])
        except WebSocketDisconnect:
            return
        except (RuntimeError, ValueError) as exc:
            await _send_ws_json_safe(websocket, {"type": "error", "output": str(exc)})
            await _close_ws_safe(websocket, code=1011)

    return router
