"""Canonical security context carried across BotBoy execution boundaries.

The context is intentionally immutable. Entry points may enrich it, but child
execution must only reduce capabilities/approval scope rather than silently
upgrade privileges.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import FrozenSet, Optional


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
    def from_legacy(
        cls,
        *,
        principal: str = "anonymous",
        org_id: str = "default",
        roles: Optional[list[str]] = None,
        request_id: str = "",
        task_id: str = "",
        parent_task_id: str = "",
        auth_source: str = "legacy",
    ) -> "SecurityContext":
        return cls(
            principal_id=principal or "anonymous",
            org_id=org_id or "default",
            roles=frozenset(roles or []),
            request_id=request_id,
            task_id=task_id,
            parent_task_id=parent_task_id,
            auth_source=auth_source,
        )

    def with_task(self, task_id: str, parent_task_id: str = "") -> "SecurityContext":
        return replace(self, task_id=task_id, parent_task_id=parent_task_id)

    def restrict_capabilities(self, allowed: set[str] | frozenset[str]) -> "SecurityContext":
        """Return a strictly less-privileged child context."""
        allowed_set = frozenset(allowed)
        return replace(
            self,
            capabilities=self.capabilities & allowed_set,
            approval_scope=self.approval_scope & allowed_set,
        )

    def has_capability(self, capability: str) -> bool:
        return capability in self.capabilities

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes
