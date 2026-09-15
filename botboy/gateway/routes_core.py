import sqlite3
import time
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

from botboy.gateway.app_context import GatewayAppContext


def create_core_router(ctx: GatewayAppContext) -> APIRouter:
    router = APIRouter()
    bot = ctx.bot

    @router.get("/health")
    async def health():
        response = {
            "status": "healthy",
            "version": bot.VERSION,
            "mode": "fastapi",
            "uptime_s": round(time.time() - getattr(bot, "_start_time", time.time()), 1),
            "components": {
                "memory": bool(bot.memory),
                "skills": bool(bot.skills),
                "cache": bool(bot.cache),
                "scheduler": bool(bot.scheduler),
                "history": bool(bot.history),
                "trace_store": bool(getattr(bot, "trace_store", None)),
                "llm": bool(bot.llm),
                "router": bool(bot.router),
                "archetypes": bool(bot.archetypes),
            },
            "security": {
                "enable_auth": ctx.auth_enabled,
                "rate_limit_enabled": bool(ctx.rate_limiter),
                "auth_api_keys_enabled": bool(getattr(ctx.security, "auth_api_keys_enabled", True)),
            },
        }
        if bot.cache:
            stats = bot.cache.stats()
            response["cache"] = {
                "hit_rate": round(stats.hit_rate, 3),
                "size": stats.size,
                "capacity": stats.capacity,
            }
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
        ctx.authorize(request.headers, request.client.host if request.client else "", require_auth=ctx.auth_enabled)
        if not bot.memory:
            raise HTTPException(status_code=503, detail="Memory not available")
        memories = bot.memory.list_all(limit=min(limit, 100), offset=offset)
        stats = bot.memory.get_stats()
        return {"memories": [item.to_dict() for item in memories], "total": stats["total"], "limit": limit, "offset": offset}

    @router.post("/api/memories")
    async def create_memory(request: Request):
        ctx.authorize(request.headers, request.client.host if request.client else "", require_auth=ctx.auth_enabled)
        if not bot.memory:
            raise HTTPException(status_code=503, detail="Memory not available")
        try:
            payload = await request.json()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid JSON")
        content = payload.get("content", "").strip()
        if not content:
            raise HTTPException(status_code=400, detail="Missing 'content'")
        memory_id = bot.memory.store(content, payload.get("metadata"))
        return {"id": memory_id, "content": content, "success": True}

    @router.get("/api/memories/search")
    async def search_memories(request: Request, q: str, limit: int = 10):
        ctx.authorize(request.headers, request.client.host if request.client else "", require_auth=ctx.auth_enabled)
        if not bot.memory:
            raise HTTPException(status_code=503, detail="Memory not available")
        results = bot.memory.search(q, limit=min(limit, 50))
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
        ctx.authorize(request.headers, request.client.host if request.client else "", require_auth=ctx.auth_enabled)
        if not bot.trace_store:
            raise HTTPException(status_code=503, detail="Trace store not available")
        records, total = bot.trace_store.list_runs(limit=min(limit, 200), offset=offset, principal=principal, request_id=request_id, status=status)
        return {"runs": [record.to_dict() for record in records], "total": total, "limit": limit, "offset": offset}

    @router.get("/api/traces/{run_id}")
    async def trace_detail(request: Request, run_id: str):
        ctx.authorize(request.headers, request.client.host if request.client else "", require_auth=ctx.auth_enabled)
        if not bot.trace_store:
            raise HTTPException(status_code=503, detail="Trace store not available")
        trace = bot.trace_store.get_run(run_id)
        if not trace:
            raise HTTPException(status_code=404, detail="Trace run not found")
        return trace

    @router.get("/api/history")
    async def history_list(request: Request, limit: int = 20, offset: int = 0, search: Optional[str] = None, success: Optional[bool] = None, request_id: Optional[str] = None):
        ctx.authorize(request.headers, request.client.host if request.client else "", require_auth=ctx.auth_enabled)
        if not bot.history:
            raise HTTPException(status_code=503, detail="History not available")
        records, total = bot.history.list(limit=min(limit, 200), offset=offset, search=search, success=success, request_id=request_id)
        return {"records": [record.to_dict() for record in records], "total": total, "limit": limit, "offset": offset}

    @router.get("/api/history/stats")
    async def history_stats(request: Request):
        ctx.authorize(request.headers, request.client.host if request.client else "", require_auth=ctx.auth_enabled)
        if not bot.history:
            raise HTTPException(status_code=503, detail="History not available")
        return bot.history.stats()

    @router.get("/api/scheduler")
    async def scheduler_list(request: Request):
        ctx.authorize(request.headers, request.client.host if request.client else "", require_auth=ctx.auth_enabled)
        if not bot.scheduler:
            raise HTTPException(status_code=503, detail="Scheduler not available")
        tasks = bot.scheduler.list_tasks()
        return {"tasks": [task.to_dict() for task in tasks], "stats": bot.scheduler.stats()}

    @router.post("/api/scheduler")
    async def scheduler_add(request: Request):
        ctx.authorize(request.headers, request.client.host if request.client else "", require_auth=ctx.auth_enabled)
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
        try:
            task_id = bot.scheduler.add(name=name, schedule=schedule, task_type=payload.get("task_type", "generic"), payload=payload.get("payload"))
            return {"task_id": task_id, "name": name, "schedule": schedule}
        except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.delete("/api/scheduler/{task_id}")
    async def scheduler_cancel(request: Request, task_id: str):
        ctx.authorize(request.headers, request.client.host if request.client else "", require_auth=ctx.auth_enabled)
        if not bot.scheduler:
            raise HTTPException(status_code=503, detail="Scheduler not available")
        bot.scheduler.cancel(task_id)
        return {"cancelled": task_id}

    return router
