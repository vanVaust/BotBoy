from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from botboy.gateway.errors import HTTPException

from botboy.security_context import SecurityContext
from botboy.security_policy import DEFAULT_CAPABILITY_POLICY

_current_principal: ContextVar[str] = ContextVar("botboy_gateway_principal", default="")
_current_roles: ContextVar[tuple[str, ...]] = ContextVar("botboy_gateway_roles", default=())
_current_org: ContextVar[str] = ContextVar("botboy_gateway_org", default="default")
_current_token_info: ContextVar[Any] = ContextVar("botboy_gateway_token_info", default=None)


def current_gateway_principal() -> str:
    return _current_principal.get()


def current_gateway_roles() -> tuple[str, ...]:
    return _current_roles.get()


def current_gateway_org() -> str:
    return _current_org.get()


class _TaskStoreAuthorizationProxy:
    """Gateway-facing task-store view with object/function-level authorization."""

    def __init__(self, store: Any, *, auth_enabled: bool) -> None:
        self._store = store
        self._auth_enabled = auth_enabled

    def _roles(self) -> set[str]:
        return {role.lower() for role in current_gateway_roles()}

    def _is_admin(self) -> bool:
        if not self._auth_enabled:
            return True
        return "admin" in self._roles()

    def _is_worker(self) -> bool:
        if not self._auth_enabled:
            return True
        return bool(self._roles() & {"admin", "worker", "system"})

    def _is_system_or_admin(self) -> bool:
        if not self._auth_enabled:
            return True
        return bool(self._roles() & {"admin", "system"})

    def _authorized(self, record: Any) -> bool:
        if record is None:
            return False
        if not self._auth_enabled:
            return True
        roles = self._roles()
        org_id = current_gateway_org()
        record_org = str(getattr(record, "org_id", "default") or "default")
        if "system" in roles:
            return True
        if "admin" in roles:
            return record_org == org_id
        principal = current_gateway_principal()
        return bool(principal) and str(getattr(record, "principal", "")) == principal and record_org == org_id

    def _require_worker_control(self) -> None:
        if not self._is_worker():
            raise HTTPException(status_code=403, detail="Worker control requires worker or admin authorization")

    def _authorized_artifact_path(self, task_id: str, file_path: str) -> Path:
        task = self._store.get_task(task_id)
        if not self._authorized(task):
            raise HTTPException(status_code=404, detail="Task not found")
        root = Path(self._store.artifact_root).expanduser().resolve()
        candidate = Path(file_path).expanduser().resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Artifact path is outside the managed artifact root") from exc
        return candidate

    def get_task(self, task_id: str):
        record = self._store.get_task(task_id)
        return record if self._authorized(record) else None

    def list_tasks(self, **kwargs):
        if not self._auth_enabled:
            return self._store.list_tasks(**kwargs)
        roles = self._roles()
        if "system" in roles:
            return self._store.list_tasks(**kwargs)
        org_id = current_gateway_org()
        principal = current_gateway_principal()
        if not principal:
            return [], 0
        scoped = dict(kwargs)
        if "admin" not in roles:
            scoped["principal"] = principal
        scoped["org_id"] = org_id
        return self._store.list_tasks(**scoped)

    def get_events(self, task_id: str, *args, **kwargs):
        if self.get_task(task_id) is None:
            return []
        return self._store.get_events(task_id, *args, **kwargs)

    def get_artifacts(self, task_id: str, *args, **kwargs):
        if self.get_task(task_id) is None:
            return []
        return self._store.get_artifacts(task_id, *args, **kwargs)

    def add_artifact_file(self, task_id: str, *args, **kwargs):
        file_path = kwargs.get("file_path")
        if file_path is None and args:
            file_path = args[0]
        self._authorized_artifact_path(task_id, str(file_path or ""))
        return self._store.add_artifact_file(task_id, *args, **kwargs)

    def write_artifact(self, task_id: str, *args, **kwargs):
        if self.get_task(task_id) is None:
            raise HTTPException(status_code=404, detail="Task not found")
        filename = str(kwargs.get("filename", ""))
        if not filename and len(args) >= 4:
            filename = str(args[3])
        root = Path(self._store.artifact_root).expanduser().resolve()
        task_dir = (root / task_id).resolve()
        target = (task_dir / filename).resolve()
        try:
            target.relative_to(task_dir)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Artifact filename escapes the task artifact directory") from exc
        return self._store.write_artifact(task_id, *args, **kwargs)

    def cancel_task(self, task_id: str, *args, **kwargs):
        if self.get_task(task_id) is None:
            return None
        return self._store.cancel_task(task_id, *args, **kwargs)

    def update_task(self, *args, **kwargs):
        """Never expose the raw task-store updater through an authenticated proxy."""
        if self._auth_enabled:
            raise HTTPException(status_code=403, detail="Direct task-store mutation 'update_task' is not authorized through the gateway proxy")
        return self._store.update_task(*args, **kwargs)

    def _node_owned_by_current_principal(self, node: Any) -> bool:
        if node is None:
            return False
        principal = current_gateway_principal()
        metadata = node.get("metadata") or {}
        owner = str(metadata.get("owner_principal", "") or "")
        worker_id = str(node.get("worker_id", "") or "")
        node_org = str(metadata.get("org_id", "default") or "default")
        return bool(principal) and node_org == current_gateway_org() and (owner == principal or worker_id == principal)

    def register_worker_node(self, *args, **kwargs):
        self._require_worker_control()
        if not self._is_system_or_admin():
            raise HTTPException(status_code=403, detail="Worker-node registration requires system or admin authorization")
        node_id = str(kwargs.get("node_id", args[0] if args else "")).strip()
        if not node_id:
            raise HTTPException(status_code=400, detail="Missing node_id")
        existing = self._store.get_worker_node(node_id)
        metadata = kwargs.get("metadata")
        metadata = dict(metadata) if isinstance(metadata, dict) else {}
        if self._auth_enabled:
            system = "system" in self._roles()
            previous = (existing or {}).get("metadata") or {}
            if existing and not system and str(previous.get("org_id", "")) != current_gateway_org():
                raise HTTPException(status_code=404, detail="Worker node not found")
            org_id = str(metadata.get("org_id") or previous.get("org_id") or current_gateway_org()) if system else current_gateway_org()
            worker_id = str(kwargs.get("worker_id", "")).strip().lower()
            queue_name = str(kwargs.get("queue_name", "")).strip() or f"{worker_id}.{node_id}"
            queue = self._store.get_execution_queue(queue_name)
            if queue and not system and str((queue.get("metadata") or {}).get("org_id", "")) != org_id:
                raise HTTPException(status_code=404, detail="Execution queue not found")
            metadata["org_id"] = org_id
            metadata["owner_principal"] = str(previous.get("owner_principal") or current_gateway_principal())
        else:
            metadata.setdefault("owner_principal", current_gateway_principal())
            metadata.setdefault("org_id", current_gateway_org())
        kwargs["metadata"] = metadata
        # Recheck the tenant under the same transaction that writes the node
        # and queue. The preliminary reads above cannot prevent a first-write race.
        kwargs["expected_org_id"] = current_gateway_org() if self._auth_enabled and "system" not in self._roles() else None
        try:
            return self._store.register_worker_node(*args, **kwargs)
        except PermissionError:
            raise HTTPException(status_code=404, detail="Worker node or execution queue not found")

    def _preserve_node_identity(self, node: Any, kwargs: dict) -> dict:
        if not self._auth_enabled:
            return kwargs
        metadata = dict(kwargs.get("metadata") or {})
        persisted = node.get("metadata") or {}
        for key in ("org_id", "owner_principal"):
            metadata[key] = persisted.get(key, "")
        return {**kwargs, "metadata": metadata}

    def heartbeat_worker_node(self, node_id: str, *args, **kwargs):
        self._require_worker_control()
        node = self._store.get_worker_node(node_id)
        if node is None:
            return None
        if not self._is_system_or_admin() and not self._node_owned_by_current_principal(node):
            return None
        if self._is_admin() and not self._auth_enabled:
            return self._store.heartbeat_worker_node(node_id, *args, **kwargs)
        metadata = node.get("metadata") or {}
        if "system" not in self._roles() and str(metadata.get("org_id", "default") or "default") != current_gateway_org():
            return None
        kwargs = self._preserve_node_identity(node, kwargs)
        return self._store.heartbeat_worker_node(node_id, *args, **kwargs)

    def drain_worker_node(self, node_id: str, *args, **kwargs):
        self._require_worker_control()
        node = self._store.get_worker_node(node_id)
        if node is None:
            return None
        if not self._is_system_or_admin() and not self._node_owned_by_current_principal(node):
            return None
        metadata = node.get("metadata") or {}
        if self._auth_enabled and "system" not in self._roles() and str(metadata.get("org_id", "default") or "default") != current_gateway_org():
            return None
        kwargs = self._preserve_node_identity(node, kwargs)
        return self._store.drain_worker_node(node_id, *args, **kwargs)

    def acquire_queue_lease(self, *args, **kwargs):
        self._require_worker_control()
        return self._store.acquire_queue_lease(*args, **kwargs)

    def renew_queue_lease(self, lease_id: str, *args, **kwargs):
        self._require_worker_control()
        lease = self._store.get_queue_lease(lease_id)
        if lease is None:
            return None
        if not self._is_admin():
            owner = str((lease.get("metadata") or {}).get("principal", "") or "")
            if not owner or owner != current_gateway_principal():
                return None
        return self._store.renew_queue_lease(lease_id, *args, **kwargs)

    def release_queue_lease(self, lease_id: str, *args, **kwargs):
        self._require_worker_control()
        lease = self._store.get_queue_lease(lease_id)
        if lease is None:
            return None
        if not self._is_admin():
            owner = str((lease.get("metadata") or {}).get("principal", "") or "")
            if not owner or owner != current_gateway_principal():
                return None
        return self._store.release_queue_lease(lease_id, *args, **kwargs)

    def list_worker_nodes(self, *args, **kwargs):
        self._require_worker_control()
        if self._auth_enabled and "system" not in self._roles():
            records = self._store.list_worker_nodes(*args, **kwargs)
            org_id = current_gateway_org()
            return [node for node in records if str((node.get("metadata") or {}).get("org_id", "default") or "default") == org_id]
        return self._store.list_worker_nodes(*args, **kwargs)

    def list_execution_queues(self, *args, **kwargs):
        self._require_worker_control()
        return self._store.list_execution_queues(*args, **kwargs)

    def list_queue_leases(self, *args, **kwargs):
        self._require_worker_control()
        return self._store.list_queue_leases(*args, **kwargs)

    def worker_node_summary(self, *args, **kwargs):
        self._require_worker_control()
        return self._store.worker_node_summary(*args, **kwargs)

    def queue_summary(self, *args, **kwargs):
        self._require_worker_control()
        return self._store.queue_summary(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._store, name)


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

    def __post_init__(self) -> None:
        original_authorize_with_roles = self.authorize_with_roles
        original_approval_context = self.approval_context
        original_store_factory = self.task_store_or_503

        def _headers_from_args(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
            if args:
                return args[0]
            return kwargs.get("headers")

        def establish_context(principal: str, roles: Any, headers: Any) -> None:
            # Clear first: a failed authorization must never reuse a prior request.
            _current_principal.set("")
            _current_roles.set(())
            _current_org.set("")
            _current_token_info.set(None)
            org_id = "default"
            if self.auth is not None:
                token_value = (headers.get("authorization") or headers.get("Authorization") or "") if headers else ""
                token = self.auth.extract_bearer_token(token_value)
                token_info = self.auth.verify(token) if token else None
                if token_info is not None and token_info.principal_id == principal:
                    org_id = token_info.org_id
                    roles = token_info.roles
                    _current_token_info.set(token_info)
                elif self.auth_enabled and principal and token:
                    raise HTTPException(status_code=401, detail="Verified tenant context required")
                elif self.auth_enabled:
                    # Anonymous login/refresh requests have no resource context.
                    return
            if not isinstance(org_id, str) or not org_id.strip():
                raise HTTPException(status_code=401, detail="Verified tenant context required")
            _current_principal.set(str(principal or ""))
            _current_roles.set(tuple(str(role).strip().lower() for role in (roles or [])))
            _current_org.set(org_id)

        def scoped_authorize(*args, **kwargs):
            principal, roles = scoped_authorize_with_roles(*args, **kwargs)
            return principal

        def scoped_authorize_with_roles(*args, **kwargs):
            _current_principal.set("")
            _current_roles.set(())
            _current_org.set("")
            _current_token_info.set(None)
            if args and isinstance(args[0], str):
                args = ({"authorization": args[0]}, *args[1:])
            elif isinstance(kwargs.get("headers"), str):
                kwargs = {**kwargs, "headers": {"authorization": kwargs["headers"]}}
            principal, roles = original_authorize_with_roles(*args, **kwargs)
            establish_context(str(principal or ""), roles, _headers_from_args(args, kwargs))
            return principal, roles

        def scoped_require_admin(*args, **kwargs):
            principal, roles = scoped_authorize_with_roles(*args, **kwargs)
            if self.auth_enabled and not {str(role).lower() for role in roles} & {"admin", "system"}:
                raise HTTPException(status_code=403, detail="Admin or system token required")

        def scoped_approval_context(*args, **kwargs):
            """Reject client-payload approval while preserving explicit approval."""
            context = dict(original_approval_context(*args, **kwargs) or {})
            if context.get("reason") == "payload" and "admin" not in {
                str(role).lower() for role in current_gateway_roles()
            }:
                context["granted"] = False
                context["explicit"] = False
                context["reason"] = "payload_approval_rejected"
            return context

        def scoped_store_factory(*args, **kwargs):
            store = original_store_factory(*args, **kwargs)
            if store is None:
                return None
            return _TaskStoreAuthorizationProxy(store, auth_enabled=self.auth_enabled)

        self.authorize = scoped_authorize
        self.require_admin = scoped_require_admin
        self.authorize_with_roles = scoped_authorize_with_roles
        self.approval_context = scoped_approval_context
        self.task_store_or_503 = scoped_store_factory

    def security_context(self, *, request_id: str = "") -> SecurityContext:
        principal = current_gateway_principal()
        org_id = current_gateway_org()
        if self.auth_enabled and (not principal or not org_id):
            raise HTTPException(status_code=401, detail="Verified execution context required")
        token_info = _current_token_info.get()
        return SecurityContext(
            credential_source=token_info.credential_source if token_info else "",
            credential_version=token_info.credential_version if token_info else "",
            principal_id=principal or "anonymous", org_id=org_id or "default",
            roles=frozenset(current_gateway_roles()),
            capabilities=DEFAULT_CAPABILITY_POLICY.for_roles(frozenset(current_gateway_roles())), auth_source="fastapi" if self.auth_enabled else "local",
            request_id=request_id,
        )
