"""Deny raw TaskStore method fallback through authenticated gateway proxies."""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from botboy.gateway.app_context import _TaskStoreAuthorizationProxy


_BLOCKED_RAW_MUTATORS = frozenset(
    {
        "create_task",
        "create_child_task",
        "recover_stale_worker_task",
        "reassign_task",
        "add_artifact_file",
        "write_artifact",
        "acquire_queue_lease",
        "renew_queue_lease",
        "release_queue_lease",
        "register_worker_node",
        "heartbeat_worker_node",
        "drain_worker_node",
    }
)


def _secure_getattr(self: _TaskStoreAuthorizationProxy, name: str) -> Any:
    """Fail closed instead of forwarding unknown TaskStore methods."""
    if self._auth_enabled:
        if name in _BLOCKED_RAW_MUTATORS:
            raise HTTPException(
                status_code=403,
                detail=f"Task-store mutation '{name}' is not exposed through the authenticated gateway proxy",
            )
        raise AttributeError(
            f"Task-store method '{name}' is not exposed through the authenticated gateway proxy"
        )
    return getattr(self._store, name)


_TaskStoreAuthorizationProxy.__getattr__ = _secure_getattr
