from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Optional

from botboy.gateway.auth import JWTAuth
from botboy.gateway.dashboard_payload import enrich_dashboard_payload as shared_enrich_dashboard_payload
from botboy.gateway.rate_limit import RateLimiter
from botboy.gateway.secrets import PrincipalStore, SecretStore
from botboy.gateway.security import validate_safe_bind
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
from botboy.resources import web_dir as bundled_web_dir


def build_simple_handler_state(
    bot,
    *,
    host: str,
    port: int,
    secret_store_cls=SecretStore,
    principal_store_cls=PrincipalStore,
) -> dict[str, Any]:
    config = getattr(bot, "config", None)
    security = getattr(config, "security", None)
    auth_enabled = bool(getattr(security, "enable_auth", False))
    validate_safe_bind(host, auth_enabled=auth_enabled, surface="gateway")

    jwt_secret = ""
    if config and hasattr(config, "resolve_jwt_secret"):
        jwt_secret = config.resolve_jwt_secret()
    elif security:
        jwt_secret = getattr(security, "jwt_secret", "")

    try:
        auth = JWTAuth(secret=jwt_secret, allow_generate=not auth_enabled)
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

    def create_principal_store() -> Optional[SecretStore]:
        if not bool(getattr(security, "auth_api_keys_enabled", True)):
            return None
        if config and hasattr(config, "resolve_api_key_store_path"):
            store_path = config.resolve_api_key_store_path()
        else:
            store_path = str(Path.home() / ".botboy" / "api_keys.db")
        try:
            return secret_store_cls(db_path=store_path)
        except (OSError, RuntimeError, sqlite3.Error):
            return None

    def create_credential_store() -> Optional[PrincipalStore]:
        if config and hasattr(config, "resolve_principal_db_path"):
            store_path = config.resolve_principal_db_path()
        else:
            store_path = str(Path.home() / ".botboy" / "principals.db")
        try:
            return principal_store_cls(db_path=store_path)
        except (OSError, RuntimeError, sqlite3.Error):
            return None

    web_dir = bundled_web_dir()
    return {
        "botboy": bot,
        "web_dir": str(web_dir) if web_dir.exists() else None,
        "server_host": host,
        "server_port": port,
        "auth": auth,
        "rate_limiter": rate_limiter,
        "principal_store": None,
        "principal_store_factory": create_principal_store,
        "credential_store": None,
        "credential_store_factory": create_credential_store,
        "security": security,
        "auth_enabled": auth_enabled,
        "request_id": "",
    }


def apply_simple_handler_state(handler_cls, state: dict[str, Any]) -> None:
    for key, value in state.items():
        setattr(handler_cls, key, value)


def close_simple_handler_stores(handler_cls) -> None:
    for attr in ("principal_store", "credential_store"):
        store = getattr(handler_cls, attr, None)
        if store is not None:
            try:
                store.close()
            except (OSError, RuntimeError, sqlite3.Error):
                pass
            setattr(handler_cls, attr, None)


def reset_simple_handler_state(handler_cls) -> None:
    apply_simple_handler_state(
        handler_cls,
        {
            "botboy": None,
            "web_dir": None,
            "auth": None,
            "rate_limiter": None,
            "principal_store": None,
            "principal_store_factory": None,
            "credential_store": None,
            "credential_store_factory": None,
            "security": None,
            "auth_enabled": False,
            "request_id": "",
        },
    )


class SimpleServerSupport:
    """Shared helper surface for stdlib gateway access, auth, and task decoration."""

    def __init__(self, handler) -> None:
        self.handler = handler

    @property
    def bot(self):
        return getattr(self.handler, "botboy", None)

    def task_store(self, optional: bool = False):
        bot = self.bot
        store = getattr(bot, "task_store", None) if bot else None
        if not store and not optional:
            if hasattr(self.handler, "_json"):
                self.handler._json({"error": "Task store not available"}, 503)
        return store

    def client_identity(self) -> str:
        forwarded = self.handler.headers.get("x-forwarded-for") or self.handler.headers.get("x-real-ip") or ""
        if forwarded:
            return forwarded.split(",")[0].strip() or self.handler.client_address[0] or "unknown"
        return self.handler.client_address[0] or "unknown"

    def extract_token(self) -> str:
        return JWTAuth.extract_bearer_token(self.handler.headers.get("authorization", ""))

    def enforce_rate_limit(self, identity: str) -> bool:
        if not getattr(self.handler, "rate_limiter", None):
            return False
        result = self.handler.rate_limiter.check(identity)
        if not result.allowed:
            retry_after = max(1, int(result.retry_after + 0.999))
            self.handler._json(
                {"error": "Rate limit exceeded", "retry_after": retry_after},
                429,
                headers={"Retry-After": str(retry_after)},
            )
            return True
        return False

    def current_token_info(self):
        token = self.extract_token()
        return self.handler.auth.verify(token) if (getattr(self.handler, "auth", None) and token) else None

    def ensure_access(self, require_auth: bool = False) -> Optional[str]:
        token_info = self.current_token_info()
        identity = token_info.principal_id if token_info else self.client_identity()

        if self.enforce_rate_limit(identity):
            return None

        if require_auth and getattr(self.handler, "auth_enabled", False) and not token_info:
            self.handler._json({"error": "Missing or invalid bearer token"}, 401)
            return None

        return identity

    def ensure_access_with_roles(self, require_auth: bool = False) -> tuple[Optional[str], list[str]]:
        token_info = self.current_token_info()
        identity = token_info.principal_id if token_info else self.client_identity()

        if self.enforce_rate_limit(identity):
            return None, []

        if require_auth and getattr(self.handler, "auth_enabled", False) and not token_info:
            self.handler._json({"error": "Missing or invalid bearer token"}, 401)
            return None, []

        return identity, list(token_info.roles) if token_info else []

    def approval_context(self, payload: Optional[dict] = None, roles: Optional[list[str]] = None) -> dict:
        header_value = (
            self.handler.headers.get("X-BotBoy-Approval")
            or self.handler.headers.get("x-botboy-approval")
            or ""
        )
        header_granted = str(header_value).strip().lower() in {"1", "true", "yes", "allow", "approved"}
        payload_granted = bool(payload.get("approval")) if isinstance(payload, dict) else False
        normalized_roles = [str(role).lower() for role in (roles or [])]
        admin_granted = "admin" in normalized_roles
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
            "source": "stdlib",
        }

    def require_admin(self) -> bool:
        token_info = self.current_token_info()
        identity = token_info.principal_id if token_info else self.client_identity()
        if self.enforce_rate_limit(identity):
            return False
        if getattr(self.handler, "auth_enabled", False) and (not token_info or "admin" not in token_info.roles):
            self.handler._json({"error": "Admin token required"}, 403)
            return False
        return True

    def get_api_key_store(self) -> Optional[SecretStore]:
        if getattr(self.handler, "principal_store", None) is not None:
            return self.handler.principal_store
        factory = getattr(type(self.handler), "principal_store_factory", None)
        if not factory:
            return None
        store = factory()
        type(self.handler).principal_store = store
        return store

    def get_credential_store(self) -> Optional[PrincipalStore]:
        if getattr(self.handler, "credential_store", None) is not None:
            return self.handler.credential_store
        factory = getattr(type(self.handler), "credential_store_factory", None)
        if not factory:
            return None
        store = factory()
        type(self.handler).credential_store = store
        return store

    def bootstrap_principal_store(self) -> Optional[PrincipalStore]:
        store = self.get_credential_store()
        if not store:
            return None
        if store.has_principals():
            return store

        bot = self.bot
        if bot and hasattr(bot.config, "resolve_bootstrap_secret"):
            expected_secret = bot.config.resolve_bootstrap_secret()
        else:
            expected_secret = getattr(getattr(self.handler, "security", None), "auth_bootstrap_secret", "")
        if not expected_secret:
            return store

        store.upsert_principal(
            getattr(getattr(self.handler, "security", None), "auth_bootstrap_user", "admin"),
            expected_secret,
            role=getattr(getattr(self.handler, "security", None), "auth_bootstrap_role", "admin"),
        )
        return store

    @staticmethod
    def principal_payload(principal) -> dict:
        return {
            "principal_id": principal.principal_id,
            "username": principal.username,
            "role": principal.role,
            "credential_type": principal.credential_type,
            "created_at": principal.created_at,
            "updated_at": principal.updated_at,
            "disabled": principal.disabled,
        }

    def worker_registry(self) -> list[dict]:
        return shared_worker_registry()

    def worker_lookup(self) -> dict[str, dict]:
        return shared_worker_lookup()

    def worker_id_from_owner(self, owner: str) -> str:
        return shared_worker_id_from_owner(owner)

    def seconds_since(self, value: str) -> int:
        return shared_seconds_since(value)

    def lease_expires_at(self, value: str, seconds: int = 900) -> str:
        return shared_lease_expires_at(value, seconds=seconds)

    def collect_task_records(self, store, root_task_id: str = "") -> list[Any]:
        return shared_collect_task_records(store, root_task_id=root_task_id)

    def task_records_by_root(self, store, root_task_id: str) -> list[Any]:
        return shared_task_records_by_root(store, root_task_id)

    def direct_child_records(self, records: list[Any], parent_task_id: str) -> list[Any]:
        return shared_direct_child_records(records, parent_task_id)

    def merge_action_catalog(self, merge_payload: dict) -> list[str]:
        return shared_merge_action_catalog(merge_payload)

    def decorate_merge_payload(self, merge_payload: dict) -> dict:
        return shared_decorate_merge_payload(merge_payload)

    def task_matches_filters(
        self,
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

    def task_matches_merge_review(
        self,
        record: Any,
        merge_review: str,
        *,
        root_records: Optional[list[Any]] = None,
    ) -> bool:
        return shared_task_matches_merge_review(self.bot, record, merge_review, root_records=root_records)

    def task_list_records(
        self,
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
            self.bot,
            store,
            limit=limit,
            offset=offset,
            status=status,
            principal=principal,
            request_id=request_id,
            root_task_id=root_task_id,
            merge_review=merge_review,
        )

    def decorate_task_record(self, store, record, root_records: Optional[list[Any]] = None) -> dict:
        return shared_decorate_task_record(self.bot, store, record, root_records=root_records)

    def build_task_graph(self, store, records: list[Any], focus_task_id: str) -> dict:
        return shared_build_task_graph(self.bot, store, records, focus_task_id)

    def task_metrics(self, store) -> dict:
        return shared_task_metrics(self.bot, store)

    def task_workers_payload(self, store) -> dict:
        return shared_task_workers_payload(self.bot, store)

    def dashboard_payload(self, bot_mode: str) -> dict:
        bot = self.bot
        payload = bot.get_dashboard_payload(mode=bot_mode)
        store = self.task_store(optional=True)
        return shared_enrich_dashboard_payload(
            payload,
            store=store,
            metrics=self.task_metrics(store) if store else {},
            workers=self.task_workers_payload(store) if store else {"available": True, "worker_count": 5, "registry": []},
            get_task=store.get_task if store else (lambda _task_id: None),
            task_records_by_root=self.task_records_by_root,
            direct_child_records=self.direct_child_records,
            decorate_task_record=self.decorate_task_record,
            build_task_graph=self.build_task_graph,
        )
