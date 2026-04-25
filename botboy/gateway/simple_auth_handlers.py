from __future__ import annotations

from botboy.gateway.auth import AuthPrincipal


def handle_auth_login(handler, payload: dict) -> None:
    bot = handler.botboy
    if not bot:
        handler._json({"error": "Bot not initialized"}, 503)
        return
    if handler._ensure_access(require_auth=False) is None:
        return
    api_key = str(payload.get("api_key", "")).strip()
    principal_display = str(payload.get("display_name", "")).strip()
    credential_source = "principal_store"
    principal_type = "user"
    principal_id = ""
    roles: list[str] = []

    if api_key:
        store = handler._get_api_key_store()
        if not store:
            handler._json({"error": "API key login is disabled"}, 503)
            return
        credential = store.validate(api_key)
        if not credential:
            handler._json({"error": "Invalid API key"}, 401)
            return
        principal_id = credential.principal_id
        roles = [credential.role]
        principal_type = "api_key"
        credential_source = "secret_store"
        principal_display = credential.label or principal_display
    else:
        username = str(payload.get("username", "")).strip()
        password = payload.get("password", "")
        if not username or not password:
            handler._json({"error": "Missing credentials"}, 400)
            return

        store = handler._bootstrap_principal_store()
        if not store:
            handler._json({"error": "Principal store unavailable"}, 503)
            return
        principal_record = store.authenticate(username, password)
        if principal_record:
            roles = [principal_record.role]
            principal_type = "admin" if principal_record.role == "admin" else "user"
            principal_id = principal_record.principal_id
            principal_display = principal_record.username
        else:
            if handler.auth_enabled and not store.has_principals():
                handler._json(
                    {"error": "Auth is enabled but no principals or bootstrap secret are configured"},
                    503,
                )
                return
            handler._json({"error": "Invalid credentials"}, 401)
            return

    principal = AuthPrincipal(
        principal_id=principal_id,
        roles=roles or ["user"],
        principal_type=principal_type,
        credential_source=credential_source,
        display_name=principal_display,
    )
    pair = handler.auth.create_pair(principal)
    handler._json(
        {
            "principal": {
                "principal_id": principal.principal_id,
                "principal_type": principal.principal_type,
                "roles": principal.roles,
                "credential_source": principal.credential_source,
                "display_name": principal.display_name,
            },
            "access_token": pair.access_token,
            "refresh_token": pair.refresh_token,
            "expires_in": pair.expires_in,
            "token_type": pair.token_type,
        }
    )


def handle_auth_refresh(handler, payload: dict) -> None:
    bot = handler.botboy
    if not bot:
        handler._json({"error": "Bot not initialized"}, 503)
        return
    if handler._ensure_access(require_auth=False) is None:
        return
    refresh_token = payload.get("refresh_token", "")
    if not refresh_token:
        handler._json({"error": "Missing refresh_token"}, 400)
        return
    pair = handler.auth.refresh(refresh_token)
    if not pair:
        handler._json({"error": "Invalid or expired refresh token"}, 401)
        return
    token_info = handler.auth.verify(pair.access_token) if handler.auth else None
    handler._json(
        {
            "principal": {
                "principal_id": token_info.principal_id if token_info else "",
                "principal_type": token_info.principal_type if token_info else "user",
                "roles": token_info.roles if token_info else [],
                "credential_source": token_info.credential_source if token_info else "jwt",
            },
            "access_token": pair.access_token,
            "expires_in": pair.expires_in,
            "token_type": pair.token_type,
        }
    )


def handle_auth_issue_api_key(handler, payload: dict) -> None:
    bot = handler.botboy
    if not bot:
        handler._json({"error": "Bot not initialized"}, 503)
        return
    if not handler._require_admin():
        return
    store = handler._get_api_key_store()
    if not store:
        handler._json({"error": "API key store unavailable"}, 503)
        return

    principal_id = str(payload.get("principal_id", "")).strip()
    role = str(payload.get("role", "user")).strip() or "user"
    label = str(payload.get("label", "")).strip()
    ttl_hours = payload.get("ttl_hours", None)
    try:
        ttl = float(ttl_hours) if ttl_hours not in (None, "") else None
    except (TypeError, ValueError):
        handler._json({"error": "ttl_hours must be numeric"}, 400)
        return

    credential = store.issue_principal_secret(
        principal_id=principal_id or label or "service",
        role=role,
        label=label,
        ttl_hours=ttl,
    )
    handler._json(
        {
            "principal": {
                "principal_id": credential.principal_id,
                "role": credential.role,
                "label": credential.label,
                "credential_type": credential.credential_type,
            },
            "full_key": credential.full_key,
            "key_id": credential.key_id,
            "created_at": credential.created_at,
            "expires_at": credential.expires_at,
        }
    )


def handle_principals_get(handler, params: dict) -> None:
    if not handler._require_admin():
        return
    store = handler._bootstrap_principal_store()
    if not store:
        handler._json({"error": "Principal store unavailable"}, 503)
        return
    raw = params.get("include_disabled", ["false"])[0].lower()
    include_disabled = raw in ("1", "true", "yes", "on")
    principals = store.list_principals(include_disabled=include_disabled)
    handler._json(
        {
            "principals": [handler._principal_payload(principal) for principal in principals],
            "total": len(principals),
            "stats": store.stats(),
        }
    )


def handle_principals_post(handler, payload: dict) -> None:
    if not handler._require_admin():
        return
    store = handler._bootstrap_principal_store()
    if not store:
        handler._json({"error": "Principal store unavailable"}, 503)
        return
    username = str(payload.get("username", "")).strip()
    password = payload.get("password", "")
    role = str(payload.get("role", "user")).strip() or "user"
    if not username or not password:
        handler._json({"error": "Missing username or password"}, 400)
        return
    principal = store.upsert_principal(username=username, password=password, role=role)
    handler._json({"principal": handler._principal_payload(principal)})


def handle_principals_delete(handler, username: str) -> None:
    if not handler._require_admin():
        return
    store = handler._bootstrap_principal_store()
    if not store:
        handler._json({"error": "Principal store unavailable"}, 503)
        return
    existing = store.get_principal(username)
    if not existing:
        handler._json({"error": "Principal not found"}, 404)
        return
    store.disable_principal(username)
    disabled = store.get_principal(username)
    handler._json({"principal": handler._principal_payload(disabled)})
