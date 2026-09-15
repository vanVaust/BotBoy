from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


_current_principal: ContextVar[str] = ContextVar("botboy_gateway_principal", default="")
_current_roles: ContextVar[tuple[str, ...]] = ContextVar("botboy_gateway_roles", default=())


def current_gateway_principal() -> str:
    return _current_principal.get()


def current_gateway_roles() -> tuple[str, ...]:
    return _current_roles.get()


class _TaskStoreAuthorizationProxy:
    """Gateway-facing task-store view with object-level authorization.

    The underlying TaskStore remains an internal service. HTTP routes receive
    this proxy so an authenticated principal cannot turn a task ID into an
    authorization bypass. Non-admin callers are restricted to their own task
    records at the store boundary, including secondary queries used to build
    task graphs, events, artifacts, and list responses.
    """

    def __init__(self, store: Any, *, auth_enabled: bool) -> None:
        self._store = store
        self._auth_enabled = auth_enabled

    def _is_admin(self) -> bool:
        if not self._auth_enabled:
            return True
        return "admin" in {role.lower() for role in current_gateway_roles()}

    def _authorized(self, record: Any) -> bool:
        if record is None or self._is_admin():
            return record is not None
        principal = current_gateway_principal()
        return bool(principal) and str(getattr(record, "principal", "")) == principal

    def get_task(self, task_id: str):
        record = self._store.get_task(task_id)
        return record if self._authorized(record) else None

    def list_tasks(self, **kwargs):
        if not self._auth_enabled or self._is_admin():
            return self._store.list_tasks(**kwargs)
        principal = current_gateway_principal()
        if not principal:
            return [], 0
        scoped = dict(kwargs)
        # The authenticated identity is authoritative; query parameters must
        # never be able to select another principal's tasks.
        scoped["principal"] = principal
        return self._store.list_tasks(**scoped)

    def get_events(self, task_id: str, *args, **kwargs):
        if self.get_task(task_id) is None:
            return []
        return self._store.get_events(task_id, *args, **kwargs)

    def get_artifacts(self, task_id: str, *args, **kwargs):
        if self.get_task(task_id) is None:
            return []
        return self._store.get_artifacts(task_id, *args, **kwargs)

    def cancel_task(self, task_id: str, *args, **kwargs):
        if self.get_task(task_id) is None:
            return None
        return self._store.cancel_task(task_id, *args, **kwargs)

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
        original_authorize = self.authorize
        original_authorize_with_roles = self.authorize_with_roles
        original_approval_context = self.approval_context
        original_store_factory = self.task_store_or_503

        def scoped_authorize(*args, **kwargs):
            principal = original_authorize(*args, **kwargs)
            _current_principal.set(str(principal or ""))
            _current_roles.set(())
            return principal

        def scoped_authorize_with_roles(*args, **kwargs):
            principal, roles = original_authorize_with_roles(*args, **kwargs)
            _current_principal.set(str(principal or ""))
            _current_roles.set(tuple(str(role) for role in (roles or [])))
            return principal, roles

        def scoped_approval_context(*args, **kwargs):
            """Build approval context without trusting a client payload boolean.

            Gateway callers may still use an explicit approval header or an
            authorized admin role. A route cannot manufacture approval merely
            by passing {"approval": true} internally; this closes the task
            resume path that previously did exactly that.
            """
            context = dict(original_approval_context(*args, **kwargs) or {})
            payload = args[1] if len(args) > 1 else kwargs.get("payload")
            roles = current_gateway_roles()
            admin = "admin" in {str(role).lower() for role in roles}
            payload_granted = isinstance(payload, dict) and bool(payload.get("approval"))
            if payload_granted and not admin:
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
        self.authorize_with_roles = scoped_authorize_with_roles
        self.approval_context = scoped_approval_context
        self.task_store_or_503 = scoped_store_factory
