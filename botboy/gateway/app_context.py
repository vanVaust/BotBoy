from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(slots=True)
class GatewayAppContext:
    bot: Any
    host: str
    port: int
    web_dir: Path
    auth: Any
    auth_enabled: bool
    rate_limiter: Any
    security: Any
    authorize: Callable[..., Any]
    authorize_with_roles: Callable[..., Any]
    approval_context: Callable[..., dict]
    require_admin: Callable[..., None]
    get_api_key_store: Callable[[], Any]
    bootstrap_principal_store: Callable[[], Any]
    principal_payload: Callable[[Any], dict]
    task_store_or_503: Callable[..., Any]
    task_detail_payload: Callable[[str], dict]
    task_merge_payload: Callable[[str], dict]
    task_merge_action_payload: Callable[..., dict]
    task_list_records: Callable[..., Any]
    task_metrics: Callable[[Any], dict]
    task_workers_payload: Callable[[Any], dict]
    task_records_by_root: Callable[[Any, str], list[Any]]
    direct_child_records: Callable[[list[Any], str], list[Any]]
    decorate_task_record: Callable[..., dict]
    build_task_graph: Callable[[Any, list[Any], str], dict]
    collect_task_records: Callable[[Any, str], list[Any]]
    worker_lookup: Callable[[], dict[str, dict]]
    worker_id_from_owner: Callable[[str], str]
    dashboard_payload: Callable[[str], dict]
