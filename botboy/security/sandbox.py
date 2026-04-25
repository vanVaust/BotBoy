"""
Sandbox executor facade with approval-oriented defaults.

Defaults are fail-closed for untrusted code and for passthrough mode.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


class SecurityLevel(Enum):
    PASSTHROUGH = 0
    BEGINNER = 1
    INTERMEDIATE = 2
    ADVANCED = 3
    EXPERT = 4


@dataclass
class SandboxResult:
    success: bool
    output: Any
    error: Optional[str]
    duration_ms: float
    tier_used: str


class SandboxExecutor:
    """
    High-level facade for code sandbox execution.

    Safe defaults:
      - BEGINNER does not imply trusted in-process execution.
      - PASSTHROUGH is disabled unless explicitly allowed.
      - Higher tiers do not silently downgrade when isolation is unavailable.
    """

    def __init__(self, level: SecurityLevel = SecurityLevel.BEGINNER,
                 *, allow_passthrough: bool = False) -> None:
        self.level = level
        self.allow_passthrough = allow_passthrough
        self._runtime = self._select_runtime(level)

    @staticmethod
    def _select_runtime(level: SecurityLevel):
        from botboy.skills.runtime import InProcessRuntime, SubprocessRuntime, IsolatedRuntime
        if level in (SecurityLevel.PASSTHROUGH, SecurityLevel.BEGINNER):
            return InProcessRuntime()
        if level == SecurityLevel.INTERMEDIATE:
            return SubprocessRuntime()
        return IsolatedRuntime()

    def execute(self, skill_name: str, code: str,
                payload: Optional[dict] = None,
                timeout: int = 10, max_memory_mb: int = 128) -> SandboxResult:
        from botboy.skills.runtime import SandboxConfig

        if self.level == SecurityLevel.PASSTHROUGH and not self.allow_passthrough:
            return SandboxResult(
                success=False,
                output=None,
                error="Passthrough execution requires explicit approval",
                duration_ms=0.0,
                tier_used="denied",
            )

        config = SandboxConfig(
            timeout_seconds=timeout,
            max_memory_mb=max_memory_mb,
            trusted_code=(self.level == SecurityLevel.PASSTHROUGH and self.allow_passthrough),
        )

        try:
            result = self._runtime.execute(
                skill_name=skill_name,
                code=code,
                payload=payload or {},
                config=config,
            )
            return SandboxResult(
                success=result.success,
                output=result.output,
                error=result.error,
                duration_ms=result.duration_ms,
                tier_used=result.runtime_used,
            )
        except Exception as exc:
            return SandboxResult(
                success=False,
                output=None,
                error=str(exc),
                duration_ms=0.0,
                tier_used="error",
            )

    @property
    def tier_name(self) -> str:
        return self.level.name.lower()

    def is_available(self) -> bool:
        """Check whether the configured tier can execute under current policy."""
        if self.level == SecurityLevel.PASSTHROUGH:
            return self.allow_passthrough
        if self.level in (SecurityLevel.BEGINNER, SecurityLevel.INTERMEDIATE):
            return True
        try:
            import subprocess
            result = subprocess.run(["docker", "info"], capture_output=True, timeout=5)
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False
