from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .security_context import SecurityContext


class AuthorizationError(PermissionError):
    """Raised when a security-sensitive operation is not authorized."""


@dataclass(frozen=True)
class AuthorizationDecision:
    allowed: bool
    reason: str = ""


def authorize_context(
    context: SecurityContext,
    *,
    required_capability: str | None = None,
    required_scope: str | None = None,
    principal_id: str | None = None,
    org_id: str | None = None,
    task_id: str | None = None,
    require_approval: bool = False,
    now: object | None = None,
) -> AuthorizationDecision:
    """Central deny-by-default object-level authorization decision.

    The caller must provide the server-derived SecurityContext. Request-body
    identity, role, tenant and approval values must never be substituted here.
    """
    if not context.principal_id:
        return AuthorizationDecision(False, "missing_principal")
    if principal_id is not None and context.principal_id != principal_id:
        return AuthorizationDecision(False, "principal_mismatch")
    if org_id is not None and context.org_id != org_id:
        return AuthorizationDecision(False, "org_mismatch")
    if task_id is not None and context.task_id != task_id:
        return AuthorizationDecision(False, "task_mismatch")
    if required_capability and not context.has_capability(required_capability):
        return AuthorizationDecision(False, "capability_missing")
    if required_scope and not context.has_scope(required_scope):
        return AuthorizationDecision(False, "scope_missing")
    if require_approval and not context.approval_valid(now=now):
        return AuthorizationDecision(False, "approval_missing_or_expired")
    return AuthorizationDecision(True, "authorized")


def require_authorized(context: SecurityContext, **kwargs: object) -> None:
    decision = authorize_context(context, **kwargs)  # type: ignore[arg-type]
    if not decision.allowed:
        raise AuthorizationError(decision.reason)


def intersect_capabilities(*capability_sets: Iterable[str]) -> frozenset[str]:
    """Return the least-privileged capability set shared by all delegates."""
    sets = [frozenset(values) for values in capability_sets]
    if not sets:
        return frozenset()
    return frozenset.intersection(*sets)
