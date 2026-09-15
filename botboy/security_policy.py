from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet


@dataclass(frozen=True)
class CapabilityPolicy:
    """Small, explicit capability policy used by security-sensitive adapters."""
    user: FrozenSet[str] = frozenset({"task.read", "task.resume"})
    admin: FrozenSet[str] = frozenset({
        "task.read", "task.resume", "task.cancel", "worker.register",
        "worker.heartbeat", "worker.drain", "worker.lease",
    })

    def for_roles(self, roles: set[str] | frozenset[str]) -> frozenset[str]:
        if "admin" in roles:
            return self.admin
        return self.user


DEFAULT_CAPABILITY_POLICY = CapabilityPolicy()
