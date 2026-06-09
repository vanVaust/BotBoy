"""Fleet and Worker Node operations for distributed BotBoy deployments."""
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Body, Header, HTTPException

from .app_context import GatewayAppContext

def create_fleet_router(ctx: GatewayAppContext) -> APIRouter:
    router = APIRouter(prefix="/api/v1/fleet", tags=["fleet"])

    def _store():
        return ctx.task_store_or_503()

    @router.get("/")
    def get_fleet_summary(authorization: str = Header(None)) -> Dict[str, Any]:
        """Get overall fleet summary including workers, nodes, and queues."""
        ctx.authorize(authorization, require_auth=ctx.auth_enabled)
        return _store().worker_node_summary()

    @router.get("/workers")
    def list_workers(
        worker_id: str = "",
        include_stale: bool = True,
        authorization: str = Header(None)
    ) -> List[Dict[str, Any]]:
        """List registered worker nodes across the multi-tenant fabric."""
        ctx.authorize(authorization, require_auth=ctx.auth_enabled)
        return _store().list_worker_nodes(worker_id=worker_id, include_stale=include_stale)

    @router.post("/workers/register")
    def register_worker_node(
        payload: Dict[str, Any] = Body(...),
        authorization: str = Header(None)
    ) -> Dict[str, Any]:
        """Register a new remote worker node to the control plane."""
        ctx.authorize(authorization, require_auth=ctx.auth_enabled)
        node_id = payload.get("node_id")
        worker_id = payload.get("worker_id")
        if not node_id or not worker_id:
            raise HTTPException(status_code=400, detail="Missing node_id or worker_id.")
        try:
            return _store().register_worker_node(
                node_id=node_id,
                worker_id=worker_id,
                display_name=payload.get("display_name", ""),
                endpoint=payload.get("endpoint", ""),
                capabilities=payload.get("capabilities"),
                queue_name=payload.get("queue_name", ""),
                lease_ttl_seconds=payload.get("lease_ttl_seconds", 900),
                max_parallelism=payload.get("max_parallelism", 1),
                max_concurrency=payload.get("max_concurrency", 0),
                metadata=payload.get("metadata"),
                last_seen_ip=payload.get("last_seen_ip", ""),
                node_status=payload.get("node_status", "ready"),
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @router.post("/workers/{node_id}/heartbeat")
    def heartbeat_worker_node(
        node_id: str,
        payload: Dict[str, Any] = Body(...),
        authorization: str = Header(None)
    ) -> Dict[str, Any]:
        """Send a heartbeat from a remote worker node."""
        ctx.authorize(authorization, require_auth=ctx.auth_enabled)
        node = _store().heartbeat_worker_node(
            node_id=node_id,
            node_status=payload.get("node_status", "ready"),
            metadata=payload.get("metadata"),
            last_seen_ip=payload.get("last_seen_ip", ""),
            health=payload.get("health", ""),
            load=payload.get("load"),
        )
        if not node:
            raise HTTPException(status_code=404, detail="Worker node not found.")
        return node

    @router.post("/workers/{node_id}/drain")
    def drain_worker_node(
        node_id: str,
        payload: Dict[str, Any] = Body(...),
        authorization: str = Header(None)
    ) -> Dict[str, Any]:
        """Set a worker node into drain mode for maintenance."""
        ctx.authorize(authorization, require_auth=ctx.auth_enabled)
        node = _store().drain_worker_node(
            node_id=node_id,
            reason=payload.get("reason", "admin operation"),
            metadata=payload.get("metadata"),
        )
        if not node:
            raise HTTPException(status_code=404, detail="Worker node not found.")
        return node

    return router
