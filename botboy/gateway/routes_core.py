import sqlite3
import time
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

from botboy.gateway.app_context import GatewayAppContext, current_gateway_org, current_gateway_principal, current_gateway_roles


def create_core_router(ctx: GatewayAppContext) -> APIRouter:
    router = APIRouter()
    bot = ctx.bot

    def _tenant_context(request: Request) -> tuple[str, str, set[str]]:
        principal, roles = ctx.authorize_with_roles(request.headers, request.client.host if request.client else "", require_auth=ctx.auth_enabled)
        return str(principal or ""), current_gateway_org(), {str(role).lower() for role in (roles or [])}

    def _is_system(roles: set[str]) -> bool:
        return "system" in roles

    def _memory_visible(memory: Any, *, principal: str, org: str, roles: set[str]) -> bool:
        metadata = getattr(memory, "metadata", None) or {}
        security = metadata.get("_botboy_security") if isinstance(metadata, dict) else None
        if not isinstance(security, dict):
            return False
        if str(security.get("org_id", "")) != org:
            return _is_system(roles)
        if _is_system(roles) or "admin" in roles:
            return True
        return str(security.get("principal_id", "")) == principal

    def _scoped_memory_metadata(metadata: Any, *, principal: str, org: str) -> dict:
        result = dict(metadata) if isinstance(metadata, dict) else {}
        result["_botboy_security"] = {"principal_id": principal, "org_id": org}
        return result

    def _scheduler_visible(task: Any, *, principal: str, org: str, roles: set[str]) -> bool:
        payload = dict(getattr(task, "payload", {}) or {})
        security = payload.get("_botboy_security")
        if not isinstance(security, dict):
            return _is_system(roles)
        if str(security.get("org_id", "")) != org:
            return _is_system(roles)
        return _is_system(roles) or "admin" in roles or str(security.get("principal_id", "")) == principal

    def _trace_visible(trace: dict, *, principal: str, org: str, roles: set[str]) -> bool:
        """Authorize a trace at the gateway boundary.

        TraceStore predates tenant-aware storage, so the gateway must never expose
        an unscoped run_id while authentication is enabled. With authentication
        disabled, the gateway is intentionally operating in its trusted-local mode;
        remote exposure is rejected by gateway readiness checks, so legacy local
        traces remain accessible for backwards-compatible local operation.
        """
        if not isinstance(trace, dict):
            return False
        if not ctx.auth_enabled:
            return True
        run = trace.get("run") or {}
        if _is_system(roles):
            return True
        run_principal = str(run.get("principal", "") or "")
        if "admin" not in roles:
            return bool(principal) and run_principal == principal
        task_id = str(run.get("task_id", "") or "")
        if not task_id:
            return run_principal == principal
        try:
            store = ctx.task_store_or_503()
            task = store.get_task(task_id)
        except Exception:
            return False
        return task is not None and str(getattr(task, "org_id", "default") or "default") == org

    @router.get("/health")
    async def health():
        response = {"status": "healthy", "version": bot.VERSION, "mode": "fastapi", "uptime_s": round(time.time() - getattr(bot, "_start_time", time.time()), 1), "components": {"memory": bool(bot.memory), "skills": bool(bot.skills), "cache": bool(bot.cache), "scheduler": bool(bot.scheduler), "history": bool(bot.history), "trace_store": bool(getattr(bot, "trace_store", None)), "llm": bool(bot.llm), "router": bool(bot.router), "archetypes": bool(bot.archetypes)}, "security": {"enable_auth": ctx.auth_enabled, "rate_limit_enabled": bool(ctx.rate_limiter), "auth_api_keys_enabled": bool(getattr(ctx.security, "auth_api_keys_enabled", True))}}
        if bot.cache:
            stats = bot.cache.stats()
            response["cache"] = {"hit_rate": round(stats.hit_rate, 3), "size": stats.size, "capacity": stats.capacity}
        return response

    @router.get("/metrics")
    async def prometheus_metrics():
        if not bot.metrics:
            return PlainTextResponse("# no metrics\n")
        body, content_type = bot.metrics.render()
        return Response(content=body, media_type=content_type)

    @router.get("/", response_class=HTMLResponse)
    async def root():
        index = ctx.web_dir / "index.html"
        if index.exists():
            return HTMLResponse(content=index.read_text(encoding="utf-8"))
        return HTMLResponse(content="<h1>BotBoy v0.6.0-dev</h1><p>Web UI not found.</p>")

    @router.get("/dashboard.html", response_class=HTMLResponse)
    async def dashboard_html():
        dashboard = ctx.web_dir / "dashboard.html"
        if dashboard.exists():
            return HTMLResponse(content=dashboard.read_text(encoding="utf-8"))
        raise HTTPException(status_code=404, detail="Dashboard UI not found")

    @router.get("/control_center_renderer.js")
    async def control_center_renderer():
        renderer = ctx.web_dir / "control_center_renderer.js"
        if renderer.exists():
            return Response(content=renderer.read_text(encoding="utf-8"), media_type="text/javascript")
        raise HTTPException(status_code=404, detail="Control Center renderer not found")

    @router.post("/api/command")
    async def execute_command(request: Request):
        principal_id, roles = ctx.authorize_with_roles(request.headers, request.client.host if request.client else "", require_auth=ctx.auth_enabled)
        try:
            payload = await request.json()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid JSON")
        command = payload.get("command", "").strip()
        if not command:
            raise HTTPException(status_code=400, detail="Missing 'command' field")
        return await bot.process_command(command, principal=principal_id, request_id=getattr(request.state, "request_id", ""), roles=roles, approval_context=ctx.approval_context(request.headers, payload, roles))

    @router.get("/api/status")
    async def api_status(request: Request):
        principal_id, roles = ctx.authorize_with_roles(request.headers, request.client.host if request.client else "", require_auth=ctx.auth_enabled)
        return await bot.process_command("status", principal=principal_id, request_id=getattr(request.state, "request_id", ""), roles=roles, approval_context=ctx.approval_context(request.headers, {}, roles))

    @router.get("/api/memories")
    async def list_memories(request: Request, limit: int = 20, offset: int = 0):
        principal, org, roles = _tenant_context(request)
        if not bot.memory:
            raise HTTPException(status_code=503, detail="Memory not available")
        fetch_limit = min(max(limit + offset, 1), 500)
        memories = bot.memory.list_all(limit=fetch_limit, offset=0)
        visible = [item for item in memories if _memory_visible(item, principal=principal, org=org, roles=roles)]
        page = visible[offset:offset + min(limit, 100)]
        return {"memories": [item.to_dict() for item in page], "total": len(visible), "limit": limit, "offset": offset}

    @router.post("/api/memories")
    async def create_memory(request: Request):
        principal, org, _roles = _tenant_context(request)
        if not bot.memory:
            raise HTTPException(status_code=503, detail="Memory not available")
        try:
            payload = await request.json()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid JSON")
        content = payload.get("content", "").strip()
        if not content:
            raise HTTPException(status_code=400, detail="Missing 'content'")
        memory_id = bot.memory.store(content, _scoped_memory_metadata(payload.get("metadata"), principal=principal, org=org))
        return {"id": memory_id, "content": content, "success": True}

    @router.get("/api/memories/search")
    async def search_memories(request: Request, q: str, limit: int = 10):
        principal, org, roles = _tenant_context(request)
        if not bot.memory:
            raise HTTPException(status_code=503, detail="Memory not available")
        candidates = bot.memory.search(q, limit=min(max(limit * 5, 50), 250))
        results = [item for item in candidates if _memory_visible(item, principal=principal, org=org, roles=roles)][:min(limit, 50)]
        return {"results": [item.to_dict() for item in results], "query": q, "count": len(results)}

    @router.get("/api/skills")
    async def list_skills():
        skills = bot.skills.list_skills() if bot.skills else []
        return {"skills": skills, "count": len(skills)}

    @router.get("/api/metrics")
    async def metrics_json():
        if not bot.metrics:
            raise HTTPException(status_code=503, detail="Metrics not available")
        return bot.metrics.to_json()

    @router.get("/api/monitoring")
    async def monitoring_json(request: Request):
        ctx.authorize(request.headers, request.client.host if request.client else "", require_auth=ctx.auth_enabled)
        payload = bot.get_monitoring_payload()
        store = getattr(bot, "task_store", None)
        if store:
            payload.setdefault("tasks", {})
            payload["tasks"].update(ctx.task_metrics(store))
            payload["workers"] = ctx.task_workers_payload(store)
        return payload

    @router.get("/api/dashboard")
    async def dashboard_json(request: Request):
        ctx.authorize(request.headers, request.client.host if request.client else "", require_auth=ctx.auth_enabled)
        return ctx.dashboard_payload("fastapi")

    @router.get("/api/traces")
    async def traces_list(request: Request, limit: int = 20, offset: int = 0, principal: Optional[str] = None, request_id: Optional[str] = None, status: Optional[str] = None):
        current_principal, org, roles = _tenant_context(request)
        if not bot.trace_store:
            raise HTTPException(status_code=503, detail="Trace store not available")
        safe_limit = min(max(int(limit or 20), 1), 200)
        safe_offset = max(int(offset or 0), 0)
        if _is_system(roles):
            records, total = bot.trace_store.list_runs(limit=safe_limit, offset=safe_offset, principal=principal, request_id=request_id, status=status)
            return {"runs": [record.to_dict() for record in records], "total": total, "limit": safe_limit, "offset": safe_offset}

        if "admin" not in roles:
            records, total = bot.trace_store.list_runs(limit=safe_limit, offset=safe_offset, principal=current_principal, request_id=request_id, status=status)
            return {"runs": [record.to_dict() for record in records], "total": total, "limit": safe_limit, "offset": safe_offset}

        # Tenant admins need cross-principal visibility within their tenant, but
        # TraceStore itself is not yet tenant-aware. Fetch a bounded candidate
        # window, then authorize each trace through the task-store tenant boundary.
        candidates, _candidate_total = bot.trace_store.list_runs(limit=500, offset=0, principal=None, request_id=request_id, status=status)
        visible = []
        for record in candidates:
            trace = bot.trace_store.get_run(record.run_id)
            if trace and _trace_visible(trace, principal=current_principal, org=org, roles=roles):
                visible.append(record)
        page = visible[safe_offset:safe_offset + safe_limit]
        return {"runs": [record.to_dict() for record in page], "total": len(visible), "limit": safe_limit, "offset": safe_offset}

    @router.get("/api/traces/{run_id}")
    async def trace_detail(request: Request, run_id: str):
        current_principal, org, roles = _tenant_context(request)
        if not bot.trace_store:
            raise HTTPException(status_code=503, detail="Trace store not available")
        trace = bot.trace_store.get_run(run_id)
        if not trace or not _trace_visible(trace, principal=current_principal, org=org, roles=roles):
            raise HTTPException(status_code=404, detail="Trace run not found")
        return trace

    @router.get("/api/history")
    async def history_list(request: Request, limit: int = 20, offset: int = 0, search: Optional[str] = None, success: Optional[bool] = None, request_id: Optional[str] = None):
        principal, org, roles = _tenant_context(request)
        if not bot.history:
            raise HTTPException(status_code=503, detail="History not available")
        scoped_principal = None if ("system" in roles or "admin" in roles) else principal
        records, total = bot.history.list(limit=min(limit, 200), offset=offset, search=search, success=success, request_id=request_id, principal=scoped_principal, org_id=org)
        return {"records": [record.to_dict() for record in records], "total": total, "limit": limit, "offset": offset}

    @router.get("/api/history/stats")
    async def history_stats(request: Request):
        principal, org, roles = _tenant_context(request)
        if not bot.history:
            raise HTTPException(status_code=503, detail="History not available")
        scoped_principal = None if ("system" in roles or "admin" in roles) else principal
        return bot.history.stats(principal=scoped_principal, org_id=org)

    @router.get("/api/scheduler")
    async def scheduler_list(request: Request):
        principal, org, roles = _tenant_context(request)
        if not bot.scheduler:
            raise HTTPException(status_code=503, detail="Scheduler not available")
        tasks = [task for task in bot.scheduler.list_tasks() if _scheduler_visible(task, principal=principal, org=org, roles=roles)]
        return {"tasks": [task.to_dict() for task in tasks], "stats": {"total": len(tasks), "active": sum(1 for task in tasks if task.enabled), "total_runs": sum(task.run_count for task in tasks)}}

    @router.post("/api/scheduler")
    async def scheduler_add(request: Request):
        principal, org, _roles = _tenant_context(request)
        if not bot.scheduler:
            raise HTTPException(status_code=503, detail="Scheduler not available")
        try:
            payload = await request.json()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid JSON")
        name = payload.get("name", "").strip()
        schedule = payload.get("schedule", "").strip()
        if not name or not schedule:
            raise HTTPException(status_code=400, detail="Missing 'name' or 'schedule'")
        scoped_payload = dict(payload.get("payload") or {})
        scoped_payload["_botboy_security"] = {"principal_id": principal, "org_id": org}
        try:
            task_id = bot.scheduler.add(name=name, schedule=schedule, task_type=payload.get("task_type", "generic"), payload=scoped_payload)
            return {"task_id": task_id, "name": name, "schedule": schedule}
        except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.delete("/api/scheduler/{task_id}")
    async def scheduler_cancel(request: Request, task_id: str):
        principal, org, roles = _tenant_context(request)
        if not bot.scheduler:
            raise HTTPException(status_code=503, detail="Scheduler not available")
        task = next((item for item in bot.scheduler.list_tasks(enabled_only=False) if item.task_id == task_id), None)
        if not task or not _scheduler_visible(task, principal=principal, org=org, roles=roles):
            raise HTTPException(status_code=404, detail="Scheduled task not found")
        bot.scheduler.cancel(task_id)
        return {"cancelled": task_id}

    return router
