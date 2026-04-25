"""Shared gateway defaults for bind host and CORS policy."""

from __future__ import annotations

import os


LOCALHOST_ALIASES = ("127.0.0.1", "localhost")
DEFAULT_GATEWAY_PORT = 8765


def _env_truthy(name: str) -> bool:
    return str(os.getenv(name, "")).strip().lower() in {"1", "true", "yes", "on", "allow"}


def default_bind_host() -> str:
    return str(os.getenv("BOTBOY_HOST", "127.0.0.1")).strip() or "127.0.0.1"


def is_loopback_host(host: str) -> bool:
    normalized = str(host or "").strip().lower().strip("[]")
    return normalized in {*LOCALHOST_ALIASES, "::1"} or normalized.startswith("127.")


def insecure_remote_override_enabled(surface: str = "gateway") -> bool:
    surface_key = f"BOTBOY_{str(surface or 'gateway').upper()}_ALLOW_INSECURE_REMOTE"
    return _env_truthy(surface_key) or _env_truthy("BOTBOY_ALLOW_INSECURE_REMOTE")


def validate_safe_bind(host: str, *, auth_enabled: bool, surface: str = "gateway") -> None:
    """Reject remote binds without auth unless an explicit operator override is set."""
    if auth_enabled or is_loopback_host(host) or insecure_remote_override_enabled(surface):
        return
    raise RuntimeError(
        f"Refusing to start {surface} on non-loopback host '{host}' with auth disabled. "
        "Enable authentication or set BOTBOY_ALLOW_INSECURE_REMOTE=1 explicitly."
    )


def resolve_cors_origins(*, host: str = "", port: int = DEFAULT_GATEWAY_PORT) -> list[str]:
    configured = str(os.getenv("BOTBOY_CORS_ALLOW_ORIGINS", "")).strip()
    if configured:
        if configured == "*":
            return ["*"]
        return [item.strip() for item in configured.split(",") if item.strip()]

    candidates = set()
    hosts = {alias for alias in LOCALHOST_ALIASES}
    if host and host not in {"0.0.0.0", "::"}:
        hosts.add(host.strip())
    for current_host in hosts:
        for current_port in {int(port), 3000, 4173, 5173, 8000, 8080}:
            candidates.add(f"http://{current_host}:{current_port}")
            candidates.add(f"https://{current_host}:{current_port}")
    return sorted(candidates)


def origin_allowed(origin: str, *, host: str = "", port: int = DEFAULT_GATEWAY_PORT) -> bool:
    normalized = str(origin or "").strip()
    if not normalized:
        return False
    allowed = resolve_cors_origins(host=host, port=port)
    return "*" in allowed or normalized in allowed
