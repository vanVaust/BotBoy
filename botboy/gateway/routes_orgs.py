"""Organization Fleet Command APIs for Enterprise Tenant Governance."""
from typing import Any, Dict, List
from fastapi import APIRouter, Body, Header, HTTPException

from .app_context import GatewayAppContext

def create_org_router(ctx: GatewayAppContext) -> APIRouter:
    router = APIRouter(prefix="/api/v1/orgs", tags=["orgs"])

    def _store():
        return ctx.task_store_or_503()

    @router.get("/")
    def list_organizations(authorization: str = Header(None)) -> List[Dict[str, Any]]:
        """List active organizations (Admin only)."""
        identity, roles = ctx.authorize_with_roles(authorization, require_auth=ctx.auth_enabled)
        if "admin" not in roles:
            raise HTTPException(status_code=403, detail="Admin required to list full org fleet")
            
        store = _store()
        # Custom query just for the control plane
        rows = store._get_conn().execute("SELECT * FROM organizations").fetchall()
        return [dict(row) for row in rows]

    @router.post("/{org_id}/suspend")
    def suspend_organization(
        org_id: str,
        payload: Dict[str, Any] = Body(...),
        authorization: str = Header(None)
    ) -> Dict[str, Any]:
        """Suspend an organization's fleet and active tasks (Incident Playbook hook)."""
        identity, roles = ctx.authorize_with_roles(authorization, require_auth=ctx.auth_enabled)
        if "admin" not in roles:
            raise HTTPException(status_code=403, detail="Admin required for Governance suspension")
            
        store = _store()
        conn = store._get_conn()
        
        # 1. Disable org
        conn.execute("UPDATE organizations SET status = 'suspended' WHERE org_id = ?", (org_id,))
        
        # 2. Halt all running tasks for this tenant
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        res = conn.execute(
            "UPDATE tasks SET status = 'blocked', updated_at = ? WHERE org_id = ? AND status IN ('running', 'queued')",
            (now, org_id)
        )
        conn.commit()
        
        return {
            "org_id": org_id,
            "status": "suspended",
            "paused_tasks_count": res.rowcount
        }

    return router
