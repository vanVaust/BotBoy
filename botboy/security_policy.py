from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet


@dataclass(frozen=True)
class CapabilityPolicy:
    """Small, explicit capability policy used by security-sensitive adapters."""
    user: FrozenSet[str] = frozenset({"task.read", "task.resume", "task.execute"})
    admin: FrozenSet[str] = frozenset({
        "task.read", "task.resume", "task.execute", "task.cancel", "worker.register",
        "worker.heartbeat", "worker.drain", "worker.lease",
    })

    worker: FrozenSet[str] = frozenset({
        "task.read", "task.execute", "worker.register", "worker.heartbeat", "worker.lease",
    })

    def for_roles(self, roles: set[str] | frozenset[str]) -> frozenset[str]:
        if "admin" in roles or "system" in roles:
            return self.admin
        if "worker" in roles:
            return self.worker
        return self.user


DEFAULT_CAPABILITY_POLICY = CapabilityPolicy()
