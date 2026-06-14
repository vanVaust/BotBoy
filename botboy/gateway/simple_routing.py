from __future__ import annotations

from typing import Optional, Tuple


GET_EXACT_ROUTES: dict[str, str] = {
    "/": "_handle_root",
    "/health": "_handle_health",
    "/metrics": "_handle_metrics",
    "/api/status": "_handle_status",
    "/api/skills": "_handle_skills",
    "/api/memories": "_handle_memories_get",
    "/api/memories/search": "_handle_memories_search",
    "/api/metrics": "_handle_metrics_json",
    "/api/monitoring": "_handle_monitoring_json",
    "/api/dashboard": "_handle_dashboard_json",
    "/api/tasks": "_handle_tasks_get",
    "/api/history": "_handle_history_get",
    "/api/history/stats": "_handle_history_stats",
    "/api/scheduler": "_handle_scheduler_get",
    "/api/principals": "_handle_principals_get",
    "/api/traces": "_handle_traces_get",
    "/api/tasks/blockers": "_handle_task_blockers",
    "/api/workers": "_handle_workers_get",
    "/api/v2/workers/nodes": "_handle_worker_nodes_get",
    "/api/v2/workers/leases": "_handle_worker_leases_get",
}

POST_EXACT_ROUTES: dict[str, str] = {
    "/api/command": "_handle_command",
    "/api/memories": "_handle_memories_post",
    "/api/auth/login": "_handle_auth_login",
    "/api/auth/refresh": "_handle_auth_refresh",
    "/api/auth/api-key": "_handle_auth_issue_api_key",
    "/api/principals": "_handle_principals_post",
    "/api/v2/workers/register": "_handle_worker_node_register",
    "/api/v2/workers/heartbeat": "_handle_worker_node_heartbeat",
    "/api/v2/workers/leases/acquire": "_handle_worker_lease_acquire",
    "/api/v2/workers/leases/renew": "_handle_worker_lease_renew",
    "/api/v2/workers/leases/release": "_handle_worker_lease_release",
}


def _normalized_path(path: str, *, keep_root: bool = True) -> str:
    normalized = path.rstrip("/")
    if keep_root:
        return normalized or "/"
    return normalized


def resolve_get_route(path: str) -> tuple[Optional[str], Optional[str]]:
    normalized = _normalized_path(path)
    exact = GET_EXACT_ROUTES.get(normalized)
    if exact:
        return exact, None
    if normalized.startswith("/api/traces/"):
        return "_handle_trace_detail", normalized.rsplit("/", 1)[-1]
    if normalized.startswith("/api/workers/"):
        return "_handle_worker_detail", normalized.rsplit("/", 1)[-1]
    if normalized.startswith("/api/tasks/") and normalized.endswith("/events"):
        return "_handle_task_events", normalized.split("/")[-2]
    if normalized.startswith("/api/tasks/") and normalized.endswith("/artifacts"):
        return "_handle_task_artifacts", normalized.split("/")[-2]
    if normalized.startswith("/api/tasks/") and normalized.endswith("/children"):
        return "_handle_task_children", normalized.split("/")[-2]
    if normalized.startswith("/api/tasks/") and normalized.endswith("/graph"):
        return "_handle_task_graph", normalized.split("/")[-2]
    if normalized.startswith("/api/tasks/") and normalized.endswith("/merge"):
        return "_handle_task_merge", normalized.split("/")[-2]
    if normalized.startswith("/api/tasks/"):
        return "_handle_task_detail", normalized.rsplit("/", 1)[-1]
    return None, None


def resolve_post_route(path: str) -> tuple[Optional[str], Optional[str]]:
    normalized = _normalized_path(path, keep_root=False)
    exact = POST_EXACT_ROUTES.get(normalized)
    if exact:
        return exact, None
    if normalized.startswith("/api/tasks/") and normalized.endswith("/resume"):
        return "_handle_task_resume", normalized.split("/")[-2]
    if normalized.startswith("/api/tasks/") and normalized.endswith("/cancel"):
        return "_handle_task_cancel", normalized.split("/")[-2]
    if normalized.startswith("/api/tasks/") and normalized.endswith("/reassign"):
        return "_handle_task_reassign", normalized.split("/")[-2]
    if normalized.startswith("/api/tasks/") and normalized.endswith("/merge/actions"):
        return "_handle_task_merge_action", normalized.split("/")[-3]
    if normalized.startswith("/api/v2/workers/") and normalized.endswith("/drain"):
        return "_handle_worker_node_drain", normalized.split("/")[-2]
    return None, None


def resolve_delete_route(path: str) -> tuple[Optional[str], Optional[str]]:
    normalized = _normalized_path(path, keep_root=False)
    if normalized.startswith("/api/principals/"):
        return "_handle_principals_delete", normalized.rsplit("/", 1)[-1]
    return None, None
