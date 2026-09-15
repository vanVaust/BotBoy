from fastapi import APIRouter, HTTPException, Request

from botboy.gateway.app_context import GatewayAppContext, current_gateway_org
from botboy.gateway.auth import AuthPrincipal


def create_auth_router(ctx: GatewayAppContext) -> APIRouter:
    router = APIRouter()

    @router.post("/api/auth/login")
    async def auth_login(request: Request):
        ctx.authorize(request.headers, request.client.host if request.client else "", require_auth=False)
        payload = await request.json()
        api_key = payload.get("api_key", "").strip()
        principal_display = payload.get("display_name", "").strip()
        credential_source = "principal_store"
        principal_type = "user"
        principal_id = ""
        roles: list[str] = []
        org_id = "default"
        if api_key:
            store = ctx.get_api_key_store()
            if not store:
                raise HTTPException(status_code=503, detail="API key login is disabled")
            credential = store.validate(api_key)
            if not credential:
                raise HTTPException(status_code=401, detail="Invalid API key")
            principal_id = credential.principal_id
            roles = [credential.role]
            principal_type = "api_key"
            credential_source = "secret_store"
            principal_display = credential.label or principal_display
            org_id = str(credential.org_id or "default")
        else:
            username = payload.get("username", "").strip()
            password = payload.get("password", "")
            if not username or not password:
                raise HTTPException(status_code=400, detail="Missing credentials")
            store = ctx.bootstrap_principal_store()
            if not store:
                raise HTTPException(status_code=503, detail="Principal store unavailable")
            principal_record = store.authenticate(username, password)
            if principal_record:
                roles = [principal_record.role]
                principal_type = "admin" if principal_record.role == "admin" else "user"
                principal_id = principal_record.principal_id
                principal_display = principal_record.username
                org_id = str(principal_record.org_id or "default")
            else:
                if ctx.auth_enabled and not store.has_principals():
                    raise HTTPException(status_code=503, detail="Auth is enabled but no principals or bootstrap secret are configured")
                raise HTTPException(status_code=401, detail="Invalid credentials")
        principal = AuthPrincipal(principal_id=principal_id, roles=roles or ["user"], principal_type=principal_type, credential_source=credential_source, display_name=principal_display, metadata={"org_id": org_id})
        pair = ctx.auth.create_pair(principal)
        return {"principal": {"principal_id": principal.principal_id, "principal_type": principal.principal_type, "roles": principal.roles, "credential_source": principal.credential_source, "display_name": principal.display_name, "org_id": org_id}, "access_token": pair.access_token, "refresh_token": pair.refresh_token, "expires_in": pair.expires_in, "token_type": pair.token_type}

    @router.post("/api/auth/refresh")
    async def auth_refresh(request: Request):
        ctx.authorize(request.headers, request.client.host if request.client else "", require_auth=False)
        payload = await request.json()
        refresh_token = payload.get("refresh_token", "")
        if not refresh_token:
            raise HTTPException(status_code=400, detail="Missing refresh_token")
        token_info = ctx.auth.verify(refresh_token, expected_type="refresh")
        if not token_info:
            raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
        principal_store = ctx.bootstrap_principal_store()
        if principal_store:
            principal_record = principal_store.get_principal(token_info.principal_id)
            if principal_record and principal_record.disabled:
                ctx.auth.revoke_token(refresh_token)
                raise HTTPException(status_code=401, detail="Principal is disabled")
        pair = ctx.auth.refresh(refresh_token)
        if not pair:
            raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
        token_info = ctx.auth.verify(pair.access_token)
        org_id = current_gateway_org()
        if principal_store:
            current = principal_store.get_principal(token_info.principal_id) if token_info else None
            if current:
                org_id = str(current.org_id or "default")
        return {"principal": {"principal_id": token_info.principal_id if token_info else "", "principal_type": token_info.principal_type if token_info else "user", "roles": token_info.roles if token_info else [], "credential_source": token_info.credential_source if token_info else "jwt", "org_id": org_id}, "access_token": pair.access_token, "expires_in": pair.expires_in, "token_type": pair.token_type}

    @router.post("/api/auth/api-key")
    async def auth_issue_api_key(request: Request):
        ctx.require_admin(request.headers, request.client.host if request.client else "")
        store = ctx.get_api_key_store()
        if not store:
            raise HTTPException(status_code=503, detail="API key store unavailable")
        payload = await request.json()
        principal_id = payload.get("principal_id", "").strip()
        role = payload.get("role", "user").strip() or "user"
        label = payload.get("label", "").strip()
        ttl_hours = payload.get("ttl_hours", None)
        try:
            ttl = float(ttl_hours) if ttl_hours not in (None, "") else None
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="ttl_hours must be numeric")
        requested_org = str(payload.get("org_id", "")).strip()
        org_id = requested_org or current_gateway_org()
        if requested_org and requested_org != current_gateway_org() and ctx.auth_enabled:
            raise HTTPException(status_code=403, detail="Cross-tenant API-key issuance is not permitted")
        credential = store.issue_principal_secret(principal_id=principal_id or label or "service", role=role, label=label, ttl_hours=ttl, org_id=org_id)
        return {"principal": {"principal_id": credential.principal_id, "role": credential.role, "label": credential.label, "credential_type": credential.credential_type, "org_id": credential.org_id}, "full_key": credential.full_key, "key_id": credential.key_id, "created_at": credential.created_at, "expires_at": credential.expires_at}

    @router.get("/api/principals")
    async def principal_list(request: Request, include_disabled: bool = False):
        ctx.require_admin(request.headers, request.client.host if request.client else "")
        store = ctx.bootstrap_principal_store()
        if not store:
            raise HTTPException(status_code=503, detail="Principal store unavailable")
        principals = store.list_principals(include_disabled=include_disabled)
        return {"principals": [ctx.principal_payload(principal) for principal in principals], "total": len(principals), "stats": store.stats()}

    @router.post("/api/principals")
    async def principal_upsert(request: Request):
        ctx.require_admin(request.headers, request.client.host if request.client else "")
        store = ctx.bootstrap_principal_store()
        if not store:
            raise HTTPException(status_code=503, detail="Principal store unavailable")
        payload = await request.json()
        username = payload.get("username", "").strip()
        password = payload.get("password", "")
        role = payload.get("role", "user").strip() or "user"
        if not username or not password:
            raise HTTPException(status_code=400, detail="Missing username or password")
        org_id = str(payload.get("org_id", "")).strip() or current_gateway_org()
        if org_id != current_gateway_org() and ctx.auth_enabled:
            raise HTTPException(status_code=403, detail="Cross-tenant principal creation is not permitted")
        principal = store.upsert_principal(username=username, password=password, role=role, org_id=org_id)
        return {"principal": ctx.principal_payload(principal)}

    @router.delete("/api/principals/{username}")
    async def principal_disable(request: Request, username: str):
        ctx.require_admin(request.headers, request.client.host if request.client else "")
        store = ctx.bootstrap_principal_store()
        if not store:
            raise HTTPException(status_code=503, detail="Principal store unavailable")
        existing = store.get_principal(username)
        if not existing:
            raise HTTPException(status_code=404, detail="Principal not found")
        store.disable_principal(username)
        disabled = store.get_principal(username)
        return {"principal": ctx.principal_payload(disabled)}

    return router
