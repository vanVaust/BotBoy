"""Deny raw TaskStore method fallback through authenticated gateway proxies."""
from __future__ import annotations

from typing import Any

from botboy.gateway.app_context import _TaskStoreAuthorizationProxy


def _secure_getattr(self: _TaskStoreAuthorizationProxy, name: str) -> Any:
    """Fail closed instead of forwarding unknown TaskStore methods.

    The gateway proxy is intentionally an allow-list surface: authorized methods
    are defined on the proxy itself or installed by the gateway security hooks.
    Falling back to the raw store would make every newly added TaskStore method
    reachable without an explicit authorization review.
    """
    if self._auth_enabled:
        raise AttributeError(
            f"Task-store method '{name}' is not exposed through the authenticated gateway proxy"
        )
    return getattr(self._store, name)


_TaskStoreAuthorizationProxy.__getattr__ = _secure_getattr
