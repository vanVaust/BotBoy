"""
BotBoy FastAPI Gateway — production HTTP/WebSocket server.

Endpoints:
    GET  /health         → system health JSON
    GET  /metrics        → Prometheus text format
    GET  /api/status     → full status dict
    POST /api/command    → execute command
    GET  /api/memories   → list memories
    POST /api/memories   → store memory
    GET  /api/memories/search → FTS5 search
    GET  /api/skills     → skill list
    GET  /api/metrics    → metrics JSON
    GET  /api/history    → command history
    GET  /api/history/stats
    GET  /api/scheduler  → scheduled tasks
    POST /api/scheduler  → add task
    DELETE /api/scheduler/{task_id}
    GET  /api/tasks     → task lifecycle list
    GET  /api/tasks/{task_id}
    GET  /api/tasks/{task_id}/events
    GET  /api/tasks/{task_id}/artifacts
    POST /api/tasks/{task_id}/resume
    POST /api/tasks/{task_id}/cancel
    POST /api/auth/login → JWT login
    POST /api/auth/refresh → token refresh
    WS   /ws/chat        → WebSocket streaming chat
"""
from __future__ import annotations

import asyncio
import json
import secrets
import site
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from botboy.gateway.security import default_bind_host, resolve_cors_origins, validate_remote_gateway_readiness
from botboy.gateway.app_context import GatewayAppContext
from botboy.gateway.dashboard_payload import enrich_dashboard_payload as shared_enrich_dashboard_payload
from botboy.gateway.merge_actions import (
    GatewayMergeActionError,
    build_merge_action_payload as shared_build_merge_action_payload,
)
from botboy.resources import web_dir as bundled_web_dir
from botboy.gateway.task_support import (
    build_task_graph as shared_build_task_graph,
    collect_task_records as shared_collect_task_records,
    decorate_merge_payload as shared_decorate_merge_payload,
    decorate_task_record as shared_decorate_task_record,
    direct_child_records as shared_direct_child_records,
    lease_expires_at as shared_lease_expires_at,
    merge_action_catalog as shared_merge_action_catalog,
    seconds_since as shared_seconds_since,
    task_list_records as shared_task_list_records,
    task_matches_filters as shared_task_matches_filters,
    task_matches_merge_review as shared_task_matches_merge_review,
    task_metrics as shared_task_metrics,
    task_records_by_root as shared_task_records_by_root,
    task_workers_payload as shared_task_workers_payload,
    worker_id_from_owner as shared_worker_id_from_owner,
    worker_lookup as shared_worker_lookup,
    worker_registry as shared_worker_registry,
)

def _repo_venv_site_packages() -> list[Path]:
    root = Path(__file__).resolve().parents[2]
    candidates = [root / ".venv" / "Lib" / "site-packages"]
    lib_root = root / ".venv" / "lib"
    if lib_root.exists():
        candidates.extend(sorted(lib_root.glob("python*/site-packages")))
    return [candidate for candidate in candidates if candidate.exists()]


def _load_fastapi_stack() -> bool:
    global FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
    global CORSMiddleware, HTMLResponse, PlainTextResponse, Response, StaticFiles, uvicorn
    try:
        from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.responses import HTMLResponse, PlainTextResponse, Response
        from fastapi.staticfiles import StaticFiles
        import uvicorn
        return True
    except ImportError:
        return False


FASTAPI_AVAILABLE = _load_fastapi_stack()
if not FASTAPI_AVAILABLE:
    for site_dir in _repo_venv_site_packages():
        if str(site_dir) not in sys.path:
            site.addsitedir(str(site_dir))
    FASTAPI_AVAILABLE = _load_fastapi_stack()

from botboy.gateway.auth import AuthPrincipal, JWTAuth
from botboy.gateway.rate_limit import RateLimiter
from botboy.gateway.secrets import PrincipalStore, SecretStore
from botboy.tasks import (
    ACTIVE_TASK_STATUSES,
    TASK_STATUS_BLOCKED,
    TASK_STATUS_CANCELLED,
    TASK_STATUS_COMPLETED,
    TASK_STATUS_FAILED,
    TASK_STATUS_QUEUED,
    TASK_STATUS_RUNNING,
    TASK_STATUS_WAITING_APPROVAL,
)


def create_app(bot, *, host: str = default_bind_host(), port: int = 8765) -> Any:
    """Create and configure the FastAPI application."""
    if not FASTAPI_AVAILABLE:
        raise ImportError("FastAPI and uvicorn required: pip install botboy[standard]")

    cors_origins = resolve_cors_origins(host=host, port=port)
    app = FastAPI(
        title="BotBoy API",
        description="BotBoy AI Agent Framework REST API",
        version=getattr(bot, "VERSION", "0.6.0-dev"),
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-Forwarded-For", "X-Real-IP", "X-BotBoy-Approval"],
        allow_credentials=False,
    )

    @app.middleware("http")
    async def attach_request_id(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or f"req-{secrets.token_hex(8)}"
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.middleware("http")
    async def csrf_protection(request: Request, call_next):
        if request.method in ("POST", "PUT", "DELETE", "PATCH"):
            # Enforce Double Submit Cookie pattern if auth cookies are present
            # or if the config enforces CSRF strictly.
            # To avoid breaking purely API-key or Bearer driven clients that don't send cookies,
            # we only strictly enforce CSRF if there is a session/auth cookie, 
            # OR if we know the request is from a browser.
            from botboy.gateway.csrf import CSRF_COOKIE_NAME, CSRF_HEADER_NAME, verify_csrf_token
            
            cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
            header_token = request.headers.get(CSRF_HEADER_NAME) or request.headers.get(CSRF_HEADER_NAME.lower())
            
            # If the user has the CSRF cookie, they MUST provide the header.
            if cookie_token:
                if not verify_csrf_token(header_token, cookie_token):
                    raise HTTPException(status_code=403, detail="CSRF token validation failed")
            
            # For now, if no cookie is present, we pass through (assuming it's a CLI/API client).
            
        response = await call_next(request)
        return response

    config = getattr(bot, "config", None)
    security = getattr(config, "security", None)
    validate_remote_gateway_readiness(config, host=host, port=port, surface="gateway")
    auth_enabled = bool(getattr(security, "enable_auth", False))

    jwt_secret = ""
    if config and hasattr(config, "resolve_jwt_secret"):
        jwt_secret = config.resolve_jwt_secret()
    elif security:
        jwt_secret = getattr(security, "jwt_secret", "")

    try:
        from botboy.gateway.secrets import TokenRevocationStore
        
        revocation_store_path = ""
        if config and hasattr(config, "resolve_token_revocation_db_path"):
            revocation_store_path = config.resolve_token_revocation_db_path()
        else:
            revocation_store_path = str(Path.home() / ".botboy" / "revoked_tokens.db")
            
        try:
            revocation_store = TokenRevocationStore(db_path=revocation_store_path)
        except (OSError, RuntimeError, sqlite3.Error):
            revocation_store = None
            
        auth = JWTAuth(secret=jwt_secret, allow_generate=not auth_enabled, revocation_store=revocation_store)
    except ValueError as exc:
        raise RuntimeError(
            "BotBoy auth is enabled, but no stable JWT secret is configured. "
            "Set security.jwt_secret, BOTBOY_JWT_SECRET, or BOTBOY_JWT_SECRET_FILE."
        ) from exc

    rate_limiter = None
    if bool(getattr(security, "rate_limit_enabled", True)):
        rate_limiter = RateLimiter(
            max_requests=max(1, int(getattr(security, "rate_limit_requests", 120))),
            window_seconds=max(1, int(getattr(security, "rate_limit_window_seconds", 3600))),
        )

    api_key_store: Optional[SecretStore] = None
    credential_store: Optional[PrincipalStore] = None

    def _get_api_key_store() -> Optional[SecretStore]:
        nonlocal api_key_store
        if not bool(getattr(security, "auth_api_keys_enabled", True)):
            return None
        if api_key_store is None:
            store_path = ""
            if config and hasattr(config, "resolve_api_key_store_path"):
                store_path = config.resolve_api_key_store_path()
            else:
                store_path = str(Path.home() / ".botboy" / "api_keys.db")
            try:
                api_key_store = SecretStore(db_path=store_path)
            except (OSError, RuntimeError, sqlite3.Error):
                return None
        return api_key_store

    def _get_credential_store() -> Optional[PrincipalStore]:
        nonlocal credential_store
        if credential_store is None:
            store_path = ""
            if config and hasattr(config, "resolve_principal_db_path"):
                store_path = config.resolve_principal_db_path()
            else:
                store_path = str(Path.home() / ".botboy" / "principals.db")
            try:
                credential_store = PrincipalStore(db_path=store_path)
            except (OSError, RuntimeError, sqlite3.Error):
                return None
        return credential_store

    app.state.bot_auth = auth
    app.state.rate_limiter = rate_limiter
    app.state.security_enabled = auth_enabled
    app.state.bot_api_key_store = _get_api_key_store
    app.state.bot_principal_store = _get_credential_store

    def _client_identity(headers: Any, client_host: str = "") -> str:
        forwarded = ""
        if headers:
            forwarded = headers.get("x-forwarded-for") or headers.get("x-real-ip") or ""
        if forwarded:
            return forwarded.split(",")[0].strip() or client_host or "unknown"
        return client_host or "unknown"

    def _extract_token(headers: Any) -> str:
        auth_header = ""
        if headers:
            auth_header = headers.get("authorization") or headers.get("Authorization") or ""
        return JWTAuth.extract_bearer_token(auth_header)

    def _enforce_rate_limit(identity: str) -> None:
        if not rate_limiter:
            return
        result = rate_limiter.check(identity)
        if not result.allowed:
            retry_after = max(1, int(result.retry_after + 0.999))
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded",
                headers={"Retry-After": str(retry_after)},
            )

    def _authorize(headers: Any, client_host: str = "", require_auth: Optional[bool] = None) -> str:
        token = _extract_token(headers)
        token_info = auth.verify(token) if token else None
        identity = token_info.principal_id if token_info else _client_identity(headers, client_host)

        _enforce_rate_limit(identity)

        if require_auth is None:
            require_auth = auth_enabled

        if require_auth:
            if not token_info:
                raise HTTPException(status_code=401, detail="Missing or invalid bearer token")
            identity = token_info.principal_id

        return identity

    def _authorize_with_roles(headers: Any, client_host: str = "", require_auth: Optional[bool] = None) -> tuple[str, list[str]]:
        token = _extract_token(headers)
        token_info = auth.verify(token) if token else None
        identity = token_info.principal_id if token_info else _client_identity(headers, client_host)

        _enforce_rate_limit(identity)

        if require_auth is None:
            require_auth = auth_enabled

        if require_auth:
            if not token_info:
                raise HTTPException(status_code=401, detail="Missing or invalid bearer token")
            identity = token_info.principal_id

        return identity, list(token_info.roles) if token_info else []

    def _approval_context(headers: Any, payload: Any, roles: list[str]) -> dict:
        header_value = ""
        if headers:
            header_value = (
                headers.get("x-botboy-approval")
                or headers.get("X-BotBoy-Approval")
                or ""
            )
        header_granted = str(header_value).strip().lower() in {"1", "true", "yes", "allow", "approved"}
        payload_granted = bool(payload.get("approval")) if isinstance(payload, dict) else False
        admin_granted = "admin" in [str(role).lower() for role in roles]
        granted = header_granted or payload_granted or admin_granted
        if admin_granted:
            reason = "admin_role"
        elif payload_granted:
            reason = "payload"
        elif header_granted:
            reason = "header"
        else:
            reason = "none"
        return {
            "granted": granted,
            "explicit": header_granted or payload_granted,
            "reason": reason,
            "source": "fastapi",
        }

    def _bootstrap_principal_store() -> Optional[PrincipalStore]:
        store = _get_credential_store()
        if not store:
            return None
        if store.has_principals():
            return store

        expected_secret = ""
        if config and hasattr(config, "resolve_bootstrap_secret"):
            expected_secret = config.resolve_bootstrap_secret()
        elif security:
            expected_secret = getattr(security, "auth_bootstrap_secret", "")
        if not expected_secret:
            return store

        store.upsert_principal(
            getattr(security, "auth_bootstrap_user", "admin"),
            expected_secret,
            role=getattr(security, "auth_bootstrap_role", "admin"),
        )
        return store

    def _principal_payload(principal: Any) -> dict:
        return {
            "principal_id": principal.principal_id,
            "username": principal.username,
            "role": principal.role,
            "credential_type": principal.credential_type,
            "created_at": principal.created_at,
            "updated_at": principal.updated_at,
            "disabled": principal.disabled,
        }

    def _require_admin(headers: Any, client_host: str = "") -> None:
        token = _extract_token(headers)
        token_info = auth.verify(token) if token else None
        identity = token_info.principal_id if token_info else _client_identity(headers, client_host)

        _enforce_rate_limit(identity)

        if auth_enabled and (not token_info or "admin" not in token_info.roles):
            raise HTTPException(status_code=403, detail="Admin token required")

    def _task_store_or_503(optional: bool = False):
        if not getattr(bot, "task_store", None):
            if optional:
                return None
            raise HTTPException(status_code=503, detail="Task store not available")
        return bot.task_store

    def _task_detail_payload(task_id: str) -> dict:
        store = _task_store_or_503()
        task = store.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        root_records = _task_records_by_root(store, task.root_task_id)
        child_records = _direct_child_records(root_records, task.task_id)
        events = store.get_events(task_id)
        artifacts = store.get_artifacts(task_id)
        return {
            "available": True,
            "task": _decorate_task_record(store, task, root_records=root_records),
            "events": [event.to_dict() for event in events],
            "artifacts": [artifact.to_dict() for artifact in artifacts],
            "children": [_decorate_task_record(store, child, root_records=root_records) for child in child_records],
            "graph": _build_task_graph(store, root_records, task.task_id),
        }

    def _merge_action_catalog(merge_payload: dict) -> list[str]:
        return shared_merge_action_catalog(merge_payload)

    def _decorate_merge_payload(merge_payload: dict) -> dict:
        return shared_decorate_merge_payload(merge_payload)

    def _task_matches_filters(
        record: Any,
        *,
        status: Optional[str],
        principal: Optional[str],
        request_id: Optional[str],
        root_task_id: Optional[str],
    ) -> bool:
        return shared_task_matches_filters(
            record,
            status=status,
            principal=principal,
            request_id=request_id,
            root_task_id=root_task_id,
        )

    def _task_matches_merge_review(record: Any, merge_review: str, *, root_records: Optional[list[Any]] = None) -> bool:
        return shared_task_matches_merge_review(bot, record, merge_review, root_records=root_records)

    def _task_list_records(
        store,
        *,
        limit: int,
        offset: int,
        status: Optional[str],
        principal: Optional[str],
        request_id: Optional[str],
        root_task_id: Optional[str],
        merge_review: str = "",
    ) -> tuple[list[Any], int, list[Any]]:
        return shared_task_list_records(
            bot,
            store,
            limit=limit,
            offset=offset,
            status=status,
            principal=principal,
            request_id=request_id,
            root_task_id=root_task_id,
            merge_review=merge_review,
        )

    def _task_merge_bulk_action_payload(
        task_id: str,
        *,
        action: str,
        principal: str = "anonymous",
        request_id: str = "",
        key: str = "",
        source: str = "",
        items: Optional[list[Any]] = None,
        keys: Optional[list[Any]] = None,
        preset: str = "",
    ) -> dict:
        store = _task_store_or_503(optional=True)
        try:
            return shared_build_merge_action_payload(
                bot,
                store=store,
                task_id=task_id,
                action=action,
                principal=principal,
                request_id=request_id,
                key=key,
                source=source,
                items=items,
                keys=keys,
                preset=preset,
                decorate_merge_payload=_decorate_merge_payload,
                decorate_task_record=_decorate_task_record,
                task_records_by_root=_task_records_by_root,
            )
        except GatewayMergeActionError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    def _task_merge_payload(task_id: str) -> dict:
        store = _task_store_or_503()
        task = store.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        merge = _decorate_merge_payload(bot.get_task_merge_payload(task_id, record=task))
        return {
            "available": bool(merge.get("available", False)),
            "task_id": task.task_id,
            "root_task_id": task.root_task_id,
            "task_status": task.status,
            "merge": merge,
        }

    def _task_merge_action_payload(
        task_id: str,
        *,
        action: str,
        key: str = "",
        source: str = "",
        items: Optional[list[Any]] = None,
        keys: Optional[list[Any]] = None,
        preset: str = "",
        principal: str = "anonymous",
        request_id: str = "",
    ) -> dict:
        return _task_merge_bulk_action_payload(
            task_id,
            action=action,
            key=key,
            source=source,
            items=items,
            keys=keys,
            preset=preset,
            principal=principal,
            request_id=request_id,
        )

    def _worker_registry() -> list[dict]:
        return shared_worker_registry()

    def _worker_lookup() -> dict[str, dict]:
        return shared_worker_lookup()

    def _worker_id_from_owner(owner: str) -> str:
        return shared_worker_id_from_owner(owner)

    def _seconds_since(value: str) -> int:
        return shared_seconds_since(value)

    def _lease_expires_at(value: str, seconds: int = 900) -> str:
        return shared_lease_expires_at(value, seconds=seconds)

    def _collect_task_records(store, root_task_id: str = "") -> list[Any]:
        return shared_collect_task_records(store, root_task_id=root_task_id)

    def _task_records_by_root(store, root_task_id: str) -> list[Any]:
        return shared_task_records_by_root(store, root_task_id)

    def _direct_child_records(records: list[Any], parent_task_id: str) -> list[Any]:
        return shared_direct_child_records(records, parent_task_id)

    def _decorate_task_record(store, record, root_records: Optional[list[Any]] = None) -> dict:
        return shared_decorate_task_record(bot, store, record, root_records=root_records)

    def _build_task_graph(store, records: list[Any], focus_task_id: str) -> dict:
        return shared_build_task_graph(bot, store, records, focus_task_id)

    def _task_metrics(store) -> dict:
        return shared_task_metrics(bot, store)

    def _task_workers_payload(store) -> dict:
        return shared_task_workers_payload(bot, store)

    def _dashboard_payload(bot_mode: str) -> dict:
        payload = bot.get_dashboard_payload(mode=bot_mode)
        store = _task_store_or_503(optional=True)
        return shared_enrich_dashboard_payload(
            payload,
            store=store,
            metrics=_task_metrics(store) if store else {},
            workers=_task_workers_payload(store) if store else {"available": True, "worker_count": 5, "registry": []},
            get_task=store.get_task if store else (lambda _task_id: None),
            task_records_by_root=_task_records_by_root,
            direct_child_records=_direct_child_records,
            decorate_task_record=_decorate_task_record,
            build_task_graph=_build_task_graph,
        )


    web_dir = bundled_web_dir()
    if web_dir.exists():
        app.mount("/static", StaticFiles(directory=str(web_dir)), name="static")

    from botboy.gateway.routes_auth import create_auth_router
    from botboy.gateway.routes_core import create_core_router
    from botboy.gateway.routes_tasks import create_task_router
    from botboy.gateway.routes_ws import create_ws_router
    from botboy.gateway.routes_fleet import create_fleet_router
    from botboy.gateway.routes_orgs import create_org_router

    route_context = GatewayAppContext(
        bot=bot,
        host=host,
        port=port,
        web_dir=web_dir,
        auth=auth,
        auth_enabled=auth_enabled,
        rate_limiter=rate_limiter,
        security=security,
        authorize=_authorize,
        authorize_with_roles=_authorize_with_roles,
        approval_context=_approval_context,
        require_admin=_require_admin,
        get_api_key_store=_get_api_key_store,
        bootstrap_principal_store=_bootstrap_principal_store,
        principal_payload=_principal_payload,
        task_store_or_503=_task_store_or_503,
        task_detail_payload=_task_detail_payload,
        task_merge_payload=_task_merge_payload,
        task_merge_action_payload=_task_merge_action_payload,
        task_list_records=_task_list_records,
        task_metrics=_task_metrics,
        task_workers_payload=_task_workers_payload,
        task_records_by_root=_task_records_by_root,
        direct_child_records=_direct_child_records,
        decorate_task_record=_decorate_task_record,
        build_task_graph=_build_task_graph,
        collect_task_records=_collect_task_records,
        worker_lookup=_worker_lookup,
        worker_id_from_owner=_worker_id_from_owner,
        dashboard_payload=_dashboard_payload,
    )

    app.include_router(create_core_router(route_context))
    app.include_router(create_task_router(route_context))
    app.include_router(create_auth_router(route_context))
    app.include_router(create_ws_router(route_context))
    app.include_router(create_fleet_router(route_context))
    app.include_router(create_org_router(route_context))

    if bot.history:
        from botboy.history import make_history_router

        router = make_history_router(bot.history)
        if router:
            app.include_router(router)

    bot._start_time = time.time()
    return app


def start_server(bot, host: str = default_bind_host(), port: int = 8765) -> None:
    """Start the FastAPI server (blocking)."""
    if not FASTAPI_AVAILABLE:
        raise ImportError("FastAPI not available. Run: pip install botboy[standard]")
    app = create_app(bot, host=host, port=port)
    print(f"[BotBoy] FastAPI server on http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="warning")
