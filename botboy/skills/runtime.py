"""Skill runtime policy with fail-closed defaults."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import time
from dataclasses import dataclass
from typing import Any, Optional, Protocol

# Static pre-check patterns blocked for untrusted skill code.
_BLOCKED_PATTERNS = [
    "__import__", "importlib", "eval(", "exec(",
    "subprocess", "os.system", "os.popen", "os.execv",
    "open(", "socket.", "urllib.", "requests.",
    "shutil.rmtree", "shutil.move",
    "ctypes", "cffi", "pickle.loads",
    "getattr(", "setattr(", "delattr(",
    "globals()", "locals()", "__builtins__",
]
_IMPORT_BLOCKLIST = {
    "subprocess", "socket", "requests", "urllib", "http", "ftplib",
    "telnetlib", "shutil", "pickle", "ctypes", "cffi",
}
_IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+([A-Za-z0-9_\.]+)", re.MULTILINE)

_MAX_OUTPUT_BYTES = 64 * 1024
_DEFAULT_TIMEOUT = 10


@dataclass
class SandboxConfig:
    timeout_seconds: int = _DEFAULT_TIMEOUT
    max_memory_mb: int = 128
    max_processes: int = 4
    allow_network: bool = False
    allow_filesystem: bool = False
    trusted_code: bool = False


@dataclass
class RuntimeResult:
    success: bool
    output: Any
    error: Optional[str] = None
    duration_ms: float = 0.0
    runtime_used: str = "unknown"


@dataclass(frozen=True)
class RuntimePolicyDecision:
    allowed: bool
    runtime_name: str
    reason: str
    needs_approval: bool = False
    denied_by_policy: bool = False

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "runtime_name": self.runtime_name,
            "reason": self.reason,
            "needs_approval": self.needs_approval,
            "denied_by_policy": self.denied_by_policy,
        }


class SkillRuntime(Protocol):
    def execute(self, skill_name: str, code: str, payload: dict,
                config: SandboxConfig) -> RuntimeResult:
        ...


def _policy_error(runtime_name: str, message: str, start: Optional[float] = None) -> RuntimeResult:
    duration_ms = 0.0
    if start is not None:
        duration_ms = (time.perf_counter() - start) * 1000
    return RuntimeResult(
        success=False,
        output=None,
        error=message,
        duration_ms=duration_ms,
        runtime_used=runtime_name,
    )


def _blocked_reason(code: str) -> Optional[str]:
    for pattern in _BLOCKED_PATTERNS:
        if pattern in code:
            return f"Blocked pattern '{pattern}' in skill code"

    for match in _IMPORT_RE.finditer(code):
        root = match.group(1).split(".", 1)[0]
        if root in _IMPORT_BLOCKLIST:
            return f"Blocked import '{root}' in skill code"

    return None


def _validate_code_request(runtime_name: str, code: str, config: SandboxConfig,
                           start: Optional[float] = None) -> Optional[RuntimeResult]:
    if not code or not code.strip():
        return _policy_error(runtime_name, "Skill code is empty", start)

    blocked_reason = _blocked_reason(code)
    if blocked_reason:
        return _policy_error(runtime_name, blocked_reason, start)

    if config.allow_network:
        return _policy_error(runtime_name, f"{runtime_name} runtime does not permit network access", start)

    if config.allow_filesystem:
        return _policy_error(
            runtime_name,
            f"{runtime_name} runtime does not permit writable filesystem access",
            start,
        )

    return None


class DeniedRuntime:
    """Policy runtime that always fails closed."""

    name = "denied"

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def execute(self, skill_name: str, code: str, payload: dict,
                config: SandboxConfig) -> RuntimeResult:
        return _policy_error(self.name, self.reason)


class InProcessRuntime:
    """
    In-process execution for explicitly trusted code only.

    This runtime is intentionally not available for untrusted or custom skill code.
    """

    name = "inprocess"

    def execute(self, skill_name: str, code: str, payload: dict,
                config: SandboxConfig) -> RuntimeResult:
        start = time.perf_counter()

        if not config.trusted_code:
            return _policy_error(
                self.name,
                "In-process execution is disabled for untrusted skill code",
                start,
            )

        validation_error = _validate_code_request(self.name, code, config, start)
        if validation_error is not None:
            return validation_error

        namespace = {
            "__builtins__": {
                "print": print, "len": len, "range": range, "str": str,
                "int": int, "float": float, "bool": bool, "list": list,
                "dict": dict, "set": set, "tuple": tuple, "abs": abs,
                "round": round, "min": min, "max": max, "sum": sum,
                "sorted": sorted, "enumerate": enumerate, "zip": zip,
                "isinstance": isinstance, "type": type,
                "True": True, "False": False, "None": None,
            },
            "payload": payload,
            "_result": None,
        }

        try:
            exec(compile(code, f"<skill:{skill_name}>", "exec"), namespace)  # noqa: S102
            result = namespace.get("_result")
            if result is None and "result" in namespace:
                result = namespace.get("result")
            return RuntimeResult(
                success=True,
                output=result,
                duration_ms=(time.perf_counter() - start) * 1000,
                runtime_used=self.name,
            )
        except Exception as exc:
            return _policy_error(self.name, str(exc), start)


_SUBPROCESS_WRAPPER = textwrap.dedent("""
import json
import sys

try:
    import resource
except ImportError:
    resource = None

if resource is not None:
    try:
        resource.setrlimit(resource.RLIMIT_AS, ({max_mem}, {max_mem}))
        resource.setrlimit(resource.RLIMIT_NPROC, ({max_proc}, {max_proc}))
        resource.setrlimit(resource.RLIMIT_CPU, ({cpu_sec}, {cpu_sec}))
    except Exception:
        pass

payload = json.loads(sys.stdin.read())
_result = None

{skill_code}

print(json.dumps({{"success": True, "result": _result if _result is not None else "done"}}))
""")


class SubprocessRuntime:
    """Resource-limited subprocess execution with strict policy gates."""

    name = "subprocess"

    def execute(self, skill_name: str, code: str, payload: dict,
                config: SandboxConfig) -> RuntimeResult:
        start = time.perf_counter()
        validation_error = _validate_code_request(self.name, code, config, start)
        if validation_error is not None:
            return validation_error

        max_mem = config.max_memory_mb * 1024 * 1024
        cpu_sec = config.timeout_seconds
        wrapper = _SUBPROCESS_WRAPPER.format(
            skill_code=code,
            max_mem=max_mem,
            max_proc=config.max_processes,
            cpu_sec=cpu_sec,
        )

        clean_env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        if os.name == "nt":
            for key in ("SystemRoot", "TEMP", "TMP", "USERPROFILE"):
                if key in os.environ:
                    clean_env[key] = os.environ[key]
        else:
            clean_env["HOME"] = os.environ.get("HOME", "/tmp")

        try:
            proc = subprocess.run(
                [sys.executable, "-c", wrapper],
                input=json.dumps(payload),
                capture_output=True,
                timeout=config.timeout_seconds,
                env=clean_env,
                text=True,
            )
            duration_ms = (time.perf_counter() - start) * 1000

            if proc.returncode != 0:
                return RuntimeResult(
                    success=False,
                    output=None,
                    error=proc.stderr[:1024],
                    duration_ms=duration_ms,
                    runtime_used=self.name,
                )

            stdout = proc.stdout[:_MAX_OUTPUT_BYTES]
            result_data = json.loads(stdout)
            return RuntimeResult(
                success=result_data.get("success", False),
                output=result_data.get("result"),
                duration_ms=duration_ms,
                runtime_used=self.name,
            )
        except subprocess.TimeoutExpired:
            return _policy_error(self.name, "Execution timeout", start)
        except Exception as exc:
            return _policy_error(self.name, str(exc), start)


class IsolatedRuntime:
    """
    Docker-backed isolation for high-risk code.

    No insecure fallback is permitted. If Docker is not available, execution fails.
    """

    name = "isolated"

    def _docker_available(self) -> bool:
        try:
            result = subprocess.run(["docker", "info"], capture_output=True, timeout=5)
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def execute(self, skill_name: str, code: str, payload: dict,
                config: SandboxConfig) -> RuntimeResult:
        start = time.perf_counter()
        validation_error = _validate_code_request(self.name, code, config, start)
        if validation_error is not None:
            return validation_error

        if not self._docker_available():
            return _policy_error(self.name, "Docker isolation unavailable for isolated runtime", start)

        script_path = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as handle:
                handle.write(
                    "import json, sys\n"
                    "payload = json.loads(sys.stdin.read())\n"
                    "_result = None\n"
                    f"{code}\n"
                    "print(json.dumps({'success': True, 'result': _result if _result is not None else 'done'}))\n"
                )
                script_path = handle.name

            docker_cmd = [
                "docker", "run", "--rm",
                "--network", "none",
                "--read-only",
                f"--memory={config.max_memory_mb}m",
                "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges",
                "-v", f"{script_path}:/skill.py:ro",
                "python:3.11-slim",
                "python", "/skill.py",
            ]

            proc = subprocess.run(
                docker_cmd,
                input=json.dumps(payload),
                capture_output=True,
                timeout=config.timeout_seconds + 10,
                text=True,
            )
            duration_ms = (time.perf_counter() - start) * 1000

            if proc.returncode != 0:
                return RuntimeResult(
                    success=False,
                    output=None,
                    error=proc.stderr[:1024],
                    duration_ms=duration_ms,
                    runtime_used=self.name,
                )

            result_data = json.loads(proc.stdout[:_MAX_OUTPUT_BYTES])
            return RuntimeResult(
                success=result_data.get("success", False),
                output=result_data.get("result"),
                duration_ms=duration_ms,
                runtime_used=self.name,
            )
        except Exception as exc:
            return _policy_error(self.name, str(exc), start)
        finally:
            if script_path:
                try:
                    os.unlink(script_path)
                except OSError:
                    pass


_INPROCESS = InProcessRuntime()
_SUBPROCESS = SubprocessRuntime()
_ISOLATED = IsolatedRuntime()

_RISK_LEVELS = {
    "BEGINNER": _INPROCESS,
    "INTERMEDIATE": _SUBPROCESS,
    "ADVANCED": _ISOLATED,
    "EXPERT": _ISOLATED,
}

_RUNTIME_BY_NAME = {
    "inprocess": _INPROCESS,
    "subprocess": _SUBPROCESS,
    "isolated": _ISOLATED,
    "denied": DeniedRuntime("Denied by policy"),
}


def get_runtime_for_skill(security_level: str) -> SkillRuntime:
    """Route to an approved runtime, otherwise fail closed."""
    level = (security_level or "").upper()
    if level in _RISK_LEVELS:
        return _RISK_LEVELS[level]
    return DeniedRuntime(f"Unknown or unapproved security level: {security_level!r}")


def get_runtime_by_name(runtime_name: str) -> SkillRuntime:
    key = str(runtime_name or "").lower()
    return _RUNTIME_BY_NAME.get(key, DeniedRuntime(f"Unknown runtime tier: {runtime_name!r}"))


def resolve_runtime_policy(
    *,
    security_level: str,
    runtime_tier: str = "auto",
    trusted: bool = False,
    allow_network: bool = False,
    allow_filesystem: bool = False,
    needs_approval: bool = False,
    approval_granted: bool = False,
    roles: Optional[list[str]] = None,
) -> RuntimePolicyDecision:
    roles = [str(role).lower() for role in (roles or [])]
    level = str(security_level or "INTERMEDIATE").upper()
    requested_tier = str(runtime_tier or "auto").lower()
    is_admin = "admin" in roles

    if requested_tier == "auto":
        if trusted and level == "BEGINNER" and not allow_network and not allow_filesystem:
            runtime_name = "inprocess"
        elif level in {"ADVANCED", "EXPERT"}:
            runtime_name = "isolated"
        elif not trusted or level == "INTERMEDIATE" or allow_network or allow_filesystem:
            runtime_name = "subprocess"
        else:
            runtime_name = "inprocess"
    else:
        runtime_name = requested_tier if requested_tier in _RUNTIME_BY_NAME else "denied"

    if not trusted and runtime_name == "inprocess":
        if level in {"ADVANCED", "EXPERT"}:
            runtime_name = "isolated"
        else:
            runtime_name = "subprocess"

    approval_required = bool(
        needs_approval
        or (not trusted)
        or level in {"INTERMEDIATE", "ADVANCED", "EXPERT"}
        or allow_network
        or allow_filesystem
    )
    if approval_required and not (approval_granted or is_admin):
        return RuntimePolicyDecision(
            allowed=False,
            runtime_name=runtime_name,
            reason="Approval required by runtime policy",
            needs_approval=True,
            denied_by_policy=True,
        )

    if runtime_name == "isolated" and not _ISOLATED._docker_available():
        return RuntimePolicyDecision(
            allowed=False,
            runtime_name=runtime_name,
            reason="Isolated runtime unavailable; policy denies unsafe fallback",
            needs_approval=approval_required,
            denied_by_policy=True,
        )

    if runtime_name == "denied":
        return RuntimePolicyDecision(
            allowed=False,
            runtime_name=runtime_name,
            reason=f"Unknown runtime tier: {runtime_tier!r}",
            needs_approval=approval_required,
            denied_by_policy=True,
        )

    return RuntimePolicyDecision(
        allowed=True,
        runtime_name=runtime_name,
        reason="Policy allowed",
        needs_approval=approval_required,
        denied_by_policy=False,
    )
