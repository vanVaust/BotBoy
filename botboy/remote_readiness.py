"""Remote/auth rollout readiness checks for operator-controlled deployments."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from botboy.gateway.auth import JWTAuth
from botboy.gateway.security import (
    default_bind_host,
    insecure_remote_override_enabled,
    is_loopback_host,
    resolve_cors_origins,
)


@dataclass(frozen=True)
class RemoteReadinessCheck:
    check_id: str
    status: str
    message: str
    action: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "status": self.status,
            "message": self.message,
            "action": self.action,
            "evidence": dict(self.evidence),
        }


def _secret_length(config) -> int:
    if config and hasattr(config, "resolve_jwt_secret"):
        return len(str(config.resolve_jwt_secret() or ""))
    security = getattr(config, "security", None)
    return len(str(getattr(security, "jwt_secret", "") or ""))


def _bootstrap_secret_configured(config) -> bool:
    if config and hasattr(config, "resolve_bootstrap_secret"):
        return bool(config.resolve_bootstrap_secret())
    return bool(getattr(getattr(config, "security", None), "auth_bootstrap_secret", ""))


def _path_configured(config, attr: str) -> bool:
    security = getattr(config, "security", None)
    raw = str(getattr(security, attr, "") or "").strip()
    if not raw:
        return False
    try:
        Path(raw).expanduser()
    except (OSError, RuntimeError):
        return False
    return True


def assess_remote_auth_readiness(
    config,
    *,
    host: str = "",
    port: int = 8765,
    mcp_http_token: str = "",
) -> dict[str, Any]:
    """Return a machine-readable readiness report for remote gateway/MCP rollout."""
    security = getattr(config, "security", None)
    bind_host = str(host or default_bind_host()).strip() or "127.0.0.1"
    remote_bind = not is_loopback_host(bind_host)
    auth_enabled = bool(getattr(security, "enable_auth", False))
    jwt_secret_len = _secret_length(config)
    cors_origins = resolve_cors_origins(host=bind_host, port=port)
    allow_all_cors = "*" in cors_origins
    mcp_token = str(mcp_http_token or os.getenv("BOTBOY_MCP_HTTP_TOKEN", "")).strip()

    checks: list[RemoteReadinessCheck] = []

    if remote_bind and not auth_enabled:
        checks.append(
            RemoteReadinessCheck(
                "gateway_remote_auth",
                "fail",
                "Gateway remote bind is configured while authentication is disabled.",
                "Enable security.enable_auth/BOTBOY_ENABLE_AUTH before binding to a non-loopback host.",
                {"host": bind_host, "auth_enabled": auth_enabled},
            )
        )
    elif remote_bind:
        checks.append(
            RemoteReadinessCheck(
                "gateway_remote_auth",
                "pass",
                "Gateway remote bind is protected by enabled authentication.",
                evidence={"host": bind_host, "auth_enabled": auth_enabled},
            )
        )
    else:
        checks.append(
            RemoteReadinessCheck(
                "gateway_bind_scope",
                "pass",
                "Gateway is bound to loopback/local scope.",
                evidence={"host": bind_host, "auth_enabled": auth_enabled},
            )
        )

    if auth_enabled and jwt_secret_len < JWTAuth.MIN_SECRET_LEN:
        checks.append(
            RemoteReadinessCheck(
                "jwt_secret",
                "fail",
                f"JWT auth is enabled but the configured secret is shorter than {JWTAuth.MIN_SECRET_LEN} characters.",
                "Set BOTBOY_JWT_SECRET or BOTBOY_JWT_SECRET_FILE to a stable high-entropy value.",
                {"secret_length": jwt_secret_len, "min_length": JWTAuth.MIN_SECRET_LEN},
            )
        )
    elif auth_enabled:
        checks.append(
            RemoteReadinessCheck(
                "jwt_secret",
                "pass",
                "JWT auth has a stable configured secret.",
                evidence={"secret_length": jwt_secret_len},
            )
        )
    else:
        checks.append(
            RemoteReadinessCheck(
                "jwt_secret",
                "warn",
                "Authentication is disabled; JWT secret is not required for local-only operation.",
                "Configure a stable JWT secret before remote rollout.",
                {"secret_length": jwt_secret_len},
            )
        )

    if auth_enabled and not _bootstrap_secret_configured(config):
        checks.append(
            RemoteReadinessCheck(
                "bootstrap_principal",
                "warn",
                "No bootstrap login secret is configured.",
                "Preseed principals/API keys or set BOTBOY_AUTH_BOOTSTRAP_SECRET for initial admin access.",
            )
        )
    elif auth_enabled:
        checks.append(RemoteReadinessCheck("bootstrap_principal", "pass", "Bootstrap/admin credential path is configured."))

    if auth_enabled and not _path_configured(config, "principal_db_path"):
        checks.append(
            RemoteReadinessCheck(
                "principal_store",
                "fail",
                "Principal store path is not configured.",
                "Set security.principal_db_path/BOTBOY_PRINCIPAL_DB_PATH to a persistent file path.",
            )
        )
    elif auth_enabled:
        checks.append(RemoteReadinessCheck("principal_store", "pass", "Principal store path is configured."))

    if auth_enabled and bool(getattr(security, "auth_api_keys_enabled", True)) and not _path_configured(
        config, "auth_api_key_store_path"
    ):
        checks.append(
            RemoteReadinessCheck(
                "api_key_store",
                "fail",
                "API-key auth is enabled but no API-key store path is configured.",
                "Set security.auth_api_key_store_path/BOTBOY_AUTH_API_KEY_STORE_PATH to a persistent file path.",
            )
        )
    elif auth_enabled and bool(getattr(security, "auth_api_keys_enabled", True)):
        checks.append(RemoteReadinessCheck("api_key_store", "pass", "API-key store path is configured."))

    if remote_bind and allow_all_cors:
        checks.append(
            RemoteReadinessCheck(
                "cors_policy",
                "fail",
                "Remote bind uses wildcard CORS.",
                "Restrict BOTBOY_CORS_ALLOW_ORIGINS to explicit operator/UI origins.",
                {"origins": cors_origins},
            )
        )
    else:
        checks.append(
            RemoteReadinessCheck(
                "cors_policy",
                "pass",
                "CORS is restricted to explicit origins.",
                evidence={"origins": cors_origins},
            )
        )

    if remote_bind and not mcp_token:
        checks.append(
            RemoteReadinessCheck(
                "mcp_http_token",
                "fail",
                "MCP HTTP remote access has no token configured.",
                "Set BOTBOY_MCP_HTTP_TOKEN before exposing MCP HTTP beyond loopback.",
            )
        )
    elif remote_bind:
        checks.append(RemoteReadinessCheck("mcp_http_token", "pass", "MCP HTTP token is configured for remote rollout."))
    else:
        checks.append(RemoteReadinessCheck("mcp_http_token", "pass", "MCP HTTP remains local-scope unless explicitly rebound."))

    if insecure_remote_override_enabled("gateway") or insecure_remote_override_enabled("mcp"):
        checks.append(
            RemoteReadinessCheck(
                "insecure_remote_override",
                "warn",
                "An insecure remote override environment variable is enabled.",
                "Unset BOTBOY_ALLOW_INSECURE_REMOTE and surface-specific override variables for controlled rollout.",
            )
        )
    else:
        checks.append(RemoteReadinessCheck("insecure_remote_override", "pass", "No insecure remote override is active."))

    failed = sum(1 for check in checks if check.status == "fail")
    warnings = sum(1 for check in checks if check.status == "warn")
    overall = "blocked" if failed else "ready" if remote_bind and auth_enabled else "local-only"
    return {
        "overall": overall,
        "remote_bind": remote_bind,
        "host": bind_host,
        "port": int(port),
        "auth_enabled": auth_enabled,
        "failed": failed,
        "warnings": warnings,
        "checks": [check.to_dict() for check in checks],
    }


def render_remote_readiness_report(report: dict[str, Any]) -> str:
    lines = [
        "Remote readiness:",
        f"  Overall: {report.get('overall')}",
        f"  Host: {report.get('host')}:{report.get('port')}",
        f"  Auth enabled: {bool(report.get('auth_enabled'))}",
        f"  Failed: {report.get('failed', 0)}  Warnings: {report.get('warnings', 0)}",
        "  Checks:",
    ]
    for check in report.get("checks", []):
        marker = str(check.get("status", "")).upper()
        lines.append(f"    [{marker}] {check.get('check_id')}: {check.get('message')}")
        action = str(check.get("action") or "").strip()
        if action:
            lines.append(f"      Action: {action}")
    return "\n".join(lines)


def handle_security_command(bot, command: str) -> dict:
    parts = command.split()
    sub = parts[1].lower() if len(parts) > 1 else "remote-readiness"
    if sub not in {"remote-readiness", "readiness", "remote"}:
        return {
            "success": False,
            "output": "Usage: security remote-readiness [host]",
            "type": "security",
        }
    host = parts[2].strip() if len(parts) > 2 else default_bind_host()
    report = assess_remote_auth_readiness(getattr(bot, "config", None), host=host)
    return {
        "success": report["overall"] != "blocked",
        "output": render_remote_readiness_report(report),
        "type": "security",
        "data": report,
    }

