"""Shared gateway defaults for bind host, CORS, and remote safety policy."""

from __future__ import annotations

import os
from typing import Any


LOCALHOST_ALIASES = ("127.0.0.1", "localhost", "::1")
WILDCARD_BIND_HOSTS = ("0.0.0.0", "::")
DEFAULT_GATEWAY_PORT = 8765


def default_bind_host() -> str:
    return str(os.getenv("BOTBOY_HOST", "127.0.0.1")).strip() or "127.0.0.1"


def normalize_bind_host(host: str = "") -> str:
    return str(host or "").strip() or default_bind_host()


def _canonical_host(host: str = "") -> str:
    normalized = normalize_bind_host(host)
    if normalized.startswith("[") and normalized.endswith("]"):
        return normalized[1:-1]
    return normalized


def _cors_host(host: str) -> str:
    return f"[{host}]" if ":" in host and not host.startswith("[") else host


def is_local_bind_host(host: str = "") -> bool:
    normalized = _canonical_host(host).lower()
    if normalized in LOCALHOST_ALIASES:
        return True
    if normalized.startswith("127."):
        return True
    return False


def is_remote_bind_host(host: str = "") -> bool:
    return not is_local_bind_host(host)


def resolve_cors_origins(*, host: str = "", port: int = DEFAULT_GATEWAY_PORT) -> list[str]:
    configured = str(os.getenv("BOTBOY_CORS_ALLOW_ORIGINS", "")).strip()
    if configured:
        if configured == "*":
            return ["*"]
        return [item.strip() for item in configured.split(",") if item.strip()]

    candidates = set()
    hosts = {alias for alias in LOCALHOST_ALIASES}
    if host and _canonical_host(host) not in WILDCARD_BIND_HOSTS:
        hosts.add(_canonical_host(host))
    for current_host in hosts:
        for current_port in {int(port), 3000, 4173, 5173, 8000, 8080}:
            origin_host = _cors_host(current_host)
            candidates.add(f"http://{origin_host}:{current_port}")
            candidates.add(f"https://{origin_host}:{current_port}")
    return sorted(candidates)


def origin_allowed(origin: str, *, host: str = "", port: int = DEFAULT_GATEWAY_PORT) -> bool:
    normalized = str(origin or "").strip()
    if not normalized:
        return False
    allowed = resolve_cors_origins(host=host, port=port)
    return "*" in allowed or normalized in allowed


def _config_auth_enabled(config: Any) -> bool:
    security = getattr(config, "security", None)
    return bool(getattr(security, "enable_auth", False))


def _config_jwt_secret_configured(config: Any) -> bool:
    if config is not None and hasattr(config, "resolve_jwt_secret"):
        try:
            return bool(str(config.resolve_jwt_secret() or "").strip())
        except (OSError, RuntimeError):
            return False
    security = getattr(config, "security", None)
    return bool(str(getattr(security, "jwt_secret", "") or "").strip())


def _config_rate_limit_enabled(config: Any) -> bool:
    security = getattr(config, "security", None)
    return bool(getattr(security, "rate_limit_enabled", True))


def build_remote_readiness_report(
    config: Any = None,
    *,
    host: str = "",
    port: int = DEFAULT_GATEWAY_PORT,
    surface: str = "gateway",
    auth_enabled: bool | None = None,
    require_jwt_secret: bool = True,
) -> dict[str, Any]:
    """Return a deterministic fail-closed assessment for non-local binds."""
    bind_host = normalize_bind_host(host)
    remote = is_remote_bind_host(bind_host)
    effective_auth = _config_auth_enabled(config) if auth_enabled is None else bool(auth_enabled)
    jwt_secret_configured = _config_jwt_secret_configured(config)
    rate_limit_enabled = _config_rate_limit_enabled(config)
    cors_origins = resolve_cors_origins(host=bind_host, port=port)
    checks: list[dict[str, str]] = []

    def add(name: str, status: str, message: str, severity: str = "error") -> None:
        checks.append(
            {
                "name": name,
                "status": status,
                "severity": severity,
                "message": message,
            }
        )

    if remote:
        add(
            "bind_host",
            "pass",
            f"{surface} binds to non-local host {bind_host}; remote safety policy applies.",
            "info",
        )
        if effective_auth:
            add("auth_required", "pass", "Authentication is enabled for non-local bind.", "info")
        else:
            add("auth_required", "fail", "Authentication must be enabled for non-local bind.")

        if require_jwt_secret:
            if jwt_secret_configured:
                add("stable_jwt_secret", "pass", "A stable JWT secret is configured.", "info")
            else:
                add(
                    "stable_jwt_secret",
                    "fail",
                    "A stable JWT secret is required for authenticated non-local gateway bind.",
                )

        if "*" in cors_origins:
            add("cors_wildcard", "fail", "Wildcard CORS is not allowed for non-local bind.")
        else:
            add("cors_wildcard", "pass", "CORS is not wildcard-permissive.", "info")

        if surface == "gateway":
            if rate_limit_enabled:
                add("rate_limit", "pass", "Rate limiting is enabled.", "info")
            else:
                add("rate_limit", "fail", "Rate limiting must stay enabled for non-local gateway bind.")
    else:
        add("bind_host", "pass", f"{surface} bind host {bind_host} is local-only.", "info")
        if not effective_auth:
            add("auth_required", "pass", "Authentication may remain optional for local-only bind.", "info")
        elif require_jwt_secret:
            if jwt_secret_configured:
                add("stable_jwt_secret", "pass", "A stable JWT secret is configured.", "info")
            else:
                add("stable_jwt_secret", "fail", "Authentication is enabled but no stable JWT secret is configured.")

    failed = [item for item in checks if item["status"] == "fail"]
    return {
        "ok": not failed,
        "surface": surface,
        "host": bind_host,
        "port": int(port),
        "remote": remote,
        "auth_enabled": effective_auth,
        "jwt_secret_configured": jwt_secret_configured if require_jwt_secret else None,
        "rate_limit_enabled": rate_limit_enabled if surface == "gateway" else None,
        "cors_origins": cors_origins,
        "checks": checks,
        "failure_count": len(failed),
    }


def format_remote_readiness_report(report: dict[str, Any]) -> str:
    status = "PASS" if report.get("ok") else "FAIL"
    lines = [
        f"BotBoy remote readiness: {status}",
        f"Surface: {report.get('surface')}",
        f"Bind: {report.get('host')}:{report.get('port')}",
    ]
    for check in report.get("checks", []):
        label = str(check.get("status", "")).upper()
        lines.append(f"- {label} {check.get('name')}: {check.get('message')}")
    return "\n".join(lines)


def validate_remote_gateway_readiness(
    config: Any,
    *,
    host: str = "",
    port: int = DEFAULT_GATEWAY_PORT,
    surface: str = "gateway",
) -> dict[str, Any]:
    report = build_remote_readiness_report(
        config,
        host=host,
        port=port,
        surface=surface,
        require_jwt_secret=True,
    )
    if not report["ok"]:
        raise RuntimeError(format_remote_readiness_report(report))
    return report


def validate_safe_bind(
    host: str,
    *,
    auth_enabled: bool = False,
    surface: str = "gateway",
    port: int = DEFAULT_GATEWAY_PORT,
) -> dict[str, Any]:
    """Compatibility guard for surfaces that do not use BotBoyConfig."""
    report = build_remote_readiness_report(
        None,
        host=host,
        port=port,
        surface=surface,
        auth_enabled=auth_enabled,
        require_jwt_secret=(surface != "mcp"),
    )
    if not report["ok"]:
        raise RuntimeError(format_remote_readiness_report(report))
    return report
