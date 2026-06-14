from __future__ import annotations

from typing import Optional

from botboy.gateway.simple_auth_handlers import (
    handle_auth_issue_api_key as shared_handle_auth_issue_api_key,
    handle_auth_login as shared_handle_auth_login,
    handle_auth_refresh as shared_handle_auth_refresh,
    handle_principals_delete as shared_handle_principals_delete,
    handle_principals_get as shared_handle_principals_get,
    handle_principals_post as shared_handle_principals_post,
)
from botboy.gateway.simple_read_handlers import (
    handle_history_get as shared_handle_history_get,
    handle_history_stats as shared_handle_history_stats,
    handle_scheduler_get as shared_handle_scheduler_get,
)
from botboy.gateway.simple_runtime_read_handlers import (
    handle_dashboard_json as shared_handle_dashboard_json,
    handle_health as shared_handle_health,
    handle_memories_get as shared_handle_memories_get,
    handle_memories_search as shared_handle_memories_search,
    handle_metrics_json as shared_handle_metrics_json,
    handle_metrics_text as shared_handle_metrics_text,
    handle_monitoring_json as shared_handle_monitoring_json,
    handle_skills as shared_handle_skills,
    handle_status as shared_handle_status,
)
from botboy.gateway.simple_runtime_write_handlers import (
    handle_command as shared_handle_command,
    handle_memories_post as shared_handle_memories_post,
)
from botboy.gateway.simple_server_support import SimpleServerSupport
from botboy.gateway.simple_static_handlers import handle_root as shared_handle_root
from botboy.gateway.simple_task_read_handlers import (
    handle_task_artifacts as shared_handle_task_artifacts,
    handle_task_blockers as shared_handle_task_blockers,
    handle_task_children as shared_handle_task_children,
    handle_task_detail as shared_handle_task_detail,
    handle_task_events as shared_handle_task_events,
    handle_task_graph as shared_handle_task_graph,
    handle_task_merge as shared_handle_task_merge,
    handle_tasks_get as shared_handle_tasks_get,
    handle_trace_detail as shared_handle_trace_detail,
    handle_traces_get as shared_handle_traces_get,
    handle_worker_detail as shared_handle_worker_detail,
    handle_worker_leases_get as shared_handle_worker_leases_get,
    handle_worker_nodes_get as shared_handle_worker_nodes_get,
    handle_workers_get as shared_handle_workers_get,
)
from botboy.gateway.simple_task_write_handlers import (
    handle_task_cancel as shared_handle_task_cancel,
    handle_task_merge_action as shared_handle_task_merge_action,
    handle_task_reassign as shared_handle_task_reassign,
    handle_task_resume as shared_handle_task_resume,
    handle_worker_lease_acquire as shared_handle_worker_lease_acquire,
    handle_worker_lease_release as shared_handle_worker_lease_release,
    handle_worker_lease_renew as shared_handle_worker_lease_renew,
    handle_worker_node_drain as shared_handle_worker_node_drain,
    handle_worker_node_heartbeat as shared_handle_worker_node_heartbeat,
    handle_worker_node_register as shared_handle_worker_node_register,
)


class SimpleHandlerSurface:
    def _client_identity(self) -> str:
        return self._support().client_identity()

    def _extract_token(self) -> str:
        return self._support().extract_token()

    def _enforce_rate_limit(self, identity: str) -> bool:
        return self._support().enforce_rate_limit(identity)

    def _ensure_access(self, require_auth: bool = False) -> Optional[str]:
        return self._support().ensure_access(require_auth=require_auth)

    def _ensure_access_with_roles(self, require_auth: bool = False) -> tuple[Optional[str], list[str]]:
        return self._support().ensure_access_with_roles(require_auth=require_auth)

    def _approval_context(self, payload: Optional[dict] = None, roles: Optional[list[str]] = None) -> dict:
        return self._support().approval_context(payload, roles)

    def _current_token_info(self):
        return self._support().current_token_info()

    def _require_admin(self) -> bool:
        return self._support().require_admin()

    def _get_api_key_store(self):
        return self._support().get_api_key_store()

    def _get_credential_store(self):
        return self._support().get_credential_store()

    def _bootstrap_principal_store(self):
        return self._support().bootstrap_principal_store()

    @staticmethod
    def _principal_payload(principal) -> dict:
        return SimpleServerSupport.principal_payload(principal)

    def _handle_root(self, params: dict) -> None:
        shared_handle_root(self, params)

    def _handle_health(self, params: dict) -> None:
        shared_handle_health(self, params)

    def _handle_metrics(self, params: dict) -> None:
        shared_handle_metrics_text(self, params)

    def _handle_status(self, params: dict) -> None:
        shared_handle_status(self, params)

    def _handle_skills(self, params: dict) -> None:
        shared_handle_skills(self, params)

    def _handle_memories_get(self, params: dict) -> None:
        shared_handle_memories_get(self, params)

    def _handle_memories_search(self, params: dict) -> None:
        shared_handle_memories_search(self, params)

    def _handle_memories_post(self, payload: dict) -> None:
        shared_handle_memories_post(self, payload)

    def _handle_metrics_json(self, params: dict) -> None:
        shared_handle_metrics_json(self, params)

    def _handle_monitoring_json(self, params: dict) -> None:
        shared_handle_monitoring_json(self, params)

    def _handle_dashboard_json(self, params: dict) -> None:
        shared_handle_dashboard_json(self, params)

    def _task_store(self, optional: bool = False):
        return self._support().task_store(optional=optional)

    def _worker_registry(self) -> list[dict]:
        return self._support().worker_registry()

    def _worker_lookup(self) -> dict[str, dict]:
        return self._support().worker_lookup()

    def _worker_id_from_owner(self, owner: str) -> str:
        return self._support().worker_id_from_owner(owner)

    def _seconds_since(self, value: str) -> int:
        return self._support().seconds_since(value)

    def _lease_expires_at(self, value: str, seconds: int = 900) -> str:
        return self._support().lease_expires_at(value, seconds=seconds)

    def _collect_task_records(self, store, root_task_id: str = "") -> list:
        return self._support().collect_task_records(store, root_task_id=root_task_id)

    def _task_records_by_root(self, store, root_task_id: str) -> list:
        return self._support().task_records_by_root(store, root_task_id)

    def _direct_child_records(self, records: list, parent_task_id: str) -> list:
        return self._support().direct_child_records(records, parent_task_id)

    def _merge_action_catalog(self, merge_payload: dict) -> list[str]:
        return self._support().merge_action_catalog(merge_payload)

    def _decorate_merge_payload(self, merge_payload: dict) -> dict:
        return self._support().decorate_merge_payload(merge_payload)

    def _task_matches_filters(
        self,
        record,
        *,
        status: Optional[str],
        principal: Optional[str],
        request_id: Optional[str],
        root_task_id: Optional[str],
    ) -> bool:
        return self._support().task_matches_filters(
            record,
            status=status,
            principal=principal,
            request_id=request_id,
            root_task_id=root_task_id,
        )

    def _task_matches_merge_review(self, record, merge_review: str, *, root_records: Optional[list] = None) -> bool:
        return self._support().task_matches_merge_review(record, merge_review, root_records=root_records)

    def _task_list_records(
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
    ) -> tuple[list, int, list]:
        return self._support().task_list_records(
            store,
            limit=limit,
            offset=offset,
            status=status,
            principal=principal,
            request_id=request_id,
            root_task_id=root_task_id,
            merge_review=merge_review,
        )

    def _decorate_task_record(self, store, record, root_records: Optional[list] = None) -> dict:
        return self._support().decorate_task_record(store, record, root_records=root_records)

    def _build_task_graph(self, store, records: list, focus_task_id: str) -> dict:
        return self._support().build_task_graph(store, records, focus_task_id)

    def _task_metrics(self, store) -> dict:
        return self._support().task_metrics(store)

    def _task_workers_payload(self, store) -> dict:
        return self._support().task_workers_payload(store)

    def _dashboard_payload(self, bot_mode: str) -> dict:
        return self._support().dashboard_payload(bot_mode)

    def _handle_traces_get(self, params: dict) -> None:
        shared_handle_traces_get(self, params)

    def _handle_trace_detail(self, run_id: str) -> None:
        shared_handle_trace_detail(self, run_id)

    def _handle_tasks_get(self, params: dict) -> None:
        shared_handle_tasks_get(self, params)

    def _handle_task_detail(self, task_id: str) -> None:
        shared_handle_task_detail(self, task_id)

    def _handle_task_merge(self, task_id: str) -> None:
        shared_handle_task_merge(self, task_id)

    def _handle_task_merge_action(self, task_id: str, payload: dict) -> None:
        shared_handle_task_merge_action(self, task_id, payload)

    def _handle_task_children(self, task_id: str) -> None:
        shared_handle_task_children(self, task_id)

    def _handle_task_graph(self, task_id: str) -> None:
        shared_handle_task_graph(self, task_id)

    def _handle_task_events(self, task_id: str) -> None:
        shared_handle_task_events(self, task_id)

    def _handle_task_blockers(self, params: dict) -> None:
        shared_handle_task_blockers(self, params)

    def _handle_workers_get(self, params: dict) -> None:
        shared_handle_workers_get(self, params)

    def _handle_worker_nodes_get(self, params: dict) -> None:
        shared_handle_worker_nodes_get(self, params)

    def _handle_worker_leases_get(self, params: dict) -> None:
        shared_handle_worker_leases_get(self, params)

    def _handle_worker_detail(self, worker_id: str) -> None:
        shared_handle_worker_detail(self, worker_id)

    def _handle_task_reassign(self, task_id: str, payload: dict) -> None:
        shared_handle_task_reassign(self, task_id, payload)

    def _handle_task_artifacts(self, task_id: str) -> None:
        shared_handle_task_artifacts(self, task_id)

    def _handle_task_resume(self, task_id: str, payload: dict) -> None:
        shared_handle_task_resume(self, task_id, payload)

    def _handle_task_cancel(self, task_id: str, payload: dict) -> None:
        shared_handle_task_cancel(self, task_id, payload)

    def _handle_worker_node_register(self, payload: dict) -> None:
        shared_handle_worker_node_register(self, payload)

    def _handle_worker_node_heartbeat(self, payload: dict) -> None:
        shared_handle_worker_node_heartbeat(self, payload)

    def _handle_worker_node_drain(self, node_id: str, payload: dict) -> None:
        shared_handle_worker_node_drain(self, node_id, payload)

    def _handle_worker_lease_acquire(self, payload: dict) -> None:
        shared_handle_worker_lease_acquire(self, payload)

    def _handle_worker_lease_renew(self, payload: dict) -> None:
        shared_handle_worker_lease_renew(self, payload)

    def _handle_worker_lease_release(self, payload: dict) -> None:
        shared_handle_worker_lease_release(self, payload)

    def _handle_history_get(self, params: dict) -> None:
        shared_handle_history_get(self, params)

    def _handle_history_stats(self, params: dict) -> None:
        shared_handle_history_stats(self, params)

    def _handle_scheduler_get(self, params: dict) -> None:
        shared_handle_scheduler_get(self, params)

    def _handle_command(self, payload: dict) -> None:
        shared_handle_command(self, payload)

    def _handle_auth_login(self, payload: dict) -> None:
        shared_handle_auth_login(self, payload)

    def _handle_auth_refresh(self, payload: dict) -> None:
        shared_handle_auth_refresh(self, payload)

    def _handle_auth_issue_api_key(self, payload: dict) -> None:
        shared_handle_auth_issue_api_key(self, payload)

    def _handle_principals_get(self, params: dict) -> None:
        shared_handle_principals_get(self, params)

    def _handle_principals_post(self, payload: dict) -> None:
        shared_handle_principals_post(self, payload)

    def _handle_principals_delete(self, username: str) -> None:
        shared_handle_principals_delete(self, username)
