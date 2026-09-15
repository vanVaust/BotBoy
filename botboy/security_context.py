"""Canonical security context carried across BotBoy execution boundaries.

The context is intentionally immutable. Entry points may enrich it, but child
execution must only reduce capabilities/approval scope rather than silently
upgrade privileges.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, FrozenSet, Optional


@dataclass(frozen=True)
class SecurityContext:
    principal_id: str = "anonymous"
    org_id: str = "default"
    roles: FrozenSet[str] = field(default_factory=frozenset)
    scopes: FrozenSet[str] = field(default_factory=frozenset)
    capabilities: FrozenSet[str] = field(default_factory=frozenset)
    auth_source: str = "local"
    session_id: str = ""
    request_id: str = ""
    task_id: str = ""
    parent_task_id: str = ""
    approval_id: str = ""
    approval_scope: FrozenSet[str] = field(default_factory=frozenset)
    approval_expires_at: Optional[str] = None
    authorization_version: str = "1"

    @classmethod
    def from_legacy(cls, *, principal: str = "anonymous", org_id: str = "default", roles: Optional[list[str]] = None, request_id: str = "", task_id: str = "", parent_task_id: str = "", auth_source: str = "legacy") -> "SecurityContext":
        return cls(principal_id=principal or "anonymous", org_id=org_id or "default", roles=frozenset(roles or []), request_id=request_id, task_id=task_id, parent_task_id=parent_task_id, auth_source=auth_source)

    @classmethod
    def from_dict(cls, value: Optional[dict[str, Any]]) -> "SecurityContext":
        """Reconstruct a context from server-persisted data."""
        value = value or {}
        return cls(
            principal_id=str(value.get("principal_id", "anonymous") or "anonymous"),
            org_id=str(value.get("org_id", "default") or "default"),
            roles=frozenset(str(item) for item in (value.get("roles") or [])),
            scopes=frozenset(str(item) for item in (value.get("scopes") or [])),
            capabilities=frozenset(str(item) for item in (value.get("capabilities") or [])),
            auth_source=str(value.get("auth_source", "local") or "local"),
            session_id=str(value.get("session_id", "") or ""),
            request_id=str(value.get("request_id", "") or ""),
            task_id=str(value.get("task_id", "") or ""),
            parent_task_id=str(value.get("parent_task_id", "") or ""),
            approval_id=str(value.get("approval_id", "") or ""),
            approval_scope=frozenset(str(item) for item in (value.get("approval_scope") or [])),
            approval_expires_at=str(value.get("approval_expires_at")) if value.get("approval_expires_at") else None,
            authorization_version=str(value.get("authorization_version", "1") or "1"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation suitable for task persistence."""
        return {
            "principal_id": self.principal_id,
            "org_id": self.org_id,
            "roles": sorted(self.roles),
            "scopes": sorted(self.scopes),
            "capabilities": sorted(self.capabilities),
            "auth_source": self.auth_source,
            "session_id": self.session_id,
            "request_id": self.request_id,
            "task_id": self.task_id,
            "parent_task_id": self.parent_task_id,
            "approval_id": self.approval_id,
            "approval_scope": sorted(self.approval_scope),
            "approval_expires_at": self.approval_expires_at,
            "authorization_version": self.authorization_version,
        }

    def with_task(self, task_id: str, parent_task_id: str = "") -> "SecurityContext":
        return replace(self, task_id=task_id, parent_task_id=parent_task_id)

    def with_approval(self, approval_id: str, *, scope: set[str] | frozenset[str], expires_at: Optional[str]) -> "SecurityContext":
        """Bind a server-issued approval to this exact execution context."""
        return replace(self, approval_id=str(approval_id or ""), approval_scope=frozenset(scope), approval_expires_at=expires_at)

    def approval_valid(self, required_capability: Optional[str] = None, *, now: Optional[datetime] = None) -> bool:
        """Check presence, expiry, and optional capability scope.

        Single-use consumption is intentionally delegated to the server-side
        approval store immediately before execution.
        """
        if not self.approval_id or not self.approval_expires_at:
            return False
        try:
            expiry = datetime.fromisoformat(self.approval_expires_at)
        except (TypeError, ValueError):
            return False
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        if expiry.astimezone(timezone.utc) <= current.astimezone(timezone.utc):
            return False
        return required_capability is None or required_capability in self.approval_scope

    def restrict_capabilities(self, allowed: set[str] | frozenset[str]) -> "SecurityContext":
        """Return a strictly less-privileged child context."""
        allowed_set = frozenset(allowed)
        return replace(self, capabilities=self.capabilities & allowed_set, approval_scope=self.approval_scope & allowed_set)

    def has_capability(self, capability: str) -> bool:
        return capability in self.capabilities

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes
