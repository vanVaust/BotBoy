"""Shared gateway defaults for bind host, CORS, and remote safety policy."""

from __future__ import annotations

import ipaddress
import os
from typing import Any


LOCALHOST_ALIASES = ("127.0.0.1", "localhost", "::1")
WILDCARD_BIND_HOSTS = ("0.0.0.0", "::")
DEFAULT_GATEWAY_PORT = 8765
FORWARDED_HEADER_NAMES = {b"x-forwarded-for", b"x-real-ip", b"forwarded"}


def _trusted_proxy_networks() -> tuple[ipaddress._BaseNetwork, ...]:
    """Return explicitly configured trusted reverse-proxy IP/CIDR networks.

    The default is empty: forwarded identity headers are therefore untrusted.
    ``BOTBOY_TRUSTED_PROXIES`` accepts a comma-separated list of IPs/CIDRs.
    """
    configured = str(os.getenv("BOTBOY_TRUSTED_PROXIES", "")).strip()
    if not configured:
        return ()
    networks: list[ipaddress._BaseNetwork] = []
    for value in configured.split(","):
        value = value.strip()
        if not value:
            continue
        try:
            networks.append(ipaddress.ip_network(value, strict=False))
        except ValueError:
            # Invalid proxy configuration must not silently create trust.
            continue
    return tuple(networks)


def _is_trusted_proxy(peer_host: str) -> bool:
    if not peer_host:
        return False
    try:
        address = ipaddress.ip_address(peer_host)
    except ValueError:
        return False
    return any(address in network for network in _trusted_proxy_networks())


class TrustedProxyHeaderMiddleware:
    """Strip untrusted forwarded identity headers at the application's boundary.

    This is a transport-neutral ASGI middleware: importing gateway security
    neither requires FastAPI nor mutates middleware used by other applications.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        client = scope.get("client") or ("", 0)
        peer_host = str(client[0] or "")
        if _is_trusted_proxy(peer_host):
            return await self.app(scope, receive, send)
        sanitized_scope = dict(scope)
        sanitized_scope["headers"] = [
            (name, value) for name, value in scope.get("headers", [])
            if name.lower() not in FORWARDED_HEADER_NAMES
        ]
        return await self.app(sanitized_scope, receive, send)


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


_PLACEHOLDER_SECRET_MARKERS = (
    "change_me", "change-me", "changeme", "replace_me", "replace-me", "replaceme",
    "your_secret", "your-secret", "yoursecret", "placeholder", "do_not_use",
    "dont_use", "insecure", "example",
)
# A secret is reduced to its "effective length": characters predictable from
# their predecessor (positions 3.. of an ascending/descending run such as
# ``abcdef`` or ``54321``) are discounted, and a periodic secret counts only
# its period.  Thresholds keep the false-reject probability for documented
# generators negligible: a run of >= 3 needs two consecutive successor
# steps (p <= (2/16)**2 per position for hex), so 32 random hex characters
# would need 13 such positions to fall below 20.  The previous
# 12-distinct-characters rule rejected ~1.7 % of ``secrets.token_hex(16)``.
_MIN_DISTINCT_SECRET_CHARS = 8
_MIN_EFFECTIVE_SECRET_CHARS = 20


def _predictable_run_chars(value: str) -> int:
    predictable = 0
    for direction in (1, -1):
        run = 1
        for prev, cur in zip(value, value[1:]):
            run = run + 1 if ord(cur) - ord(prev) == direction else 1
            if run >= 3:
                predictable += 1 if run > 3 else 2
    return predictable


def _shortest_period(value: str) -> int:
    # Smallest p with value[i] == value[i - p] for all i >= p (KMP failure
    # function, O(n)).
    n = len(value)
    fail = [0] * n
    k = 0
    for i in range(1, n):
        while k and value[i] != value[k]:
            k = fail[k - 1]
        if value[i] == value[k]:
            k += 1
        fail[i] = k
    return n - fail[-1] if n else 0


def _effective_secret_length(value: str) -> int:
    effective = len(value) - _predictable_run_chars(value)
    period = _shortest_period(value)
    if period <= len(value) // 2:
        effective = min(effective, period)
    return effective


def secret_strength_problem(secret: str) -> str:
    """Reject known published placeholders and conspicuously weak JWT secrets.

    This is a safety screen, not a way to prove entropy.  Operators must still
    generate the secret using a cryptographically secure random generator.
    """
    value = str(secret or "").strip()
    if len(value) < 32:
        return "secret must be at least 32 characters"
    if any(marker in value.lower() for marker in _PLACEHOLDER_SECRET_MARKERS):
        return "secret looks like a documentation placeholder; generate a random one"
    if len(set(value)) < _MIN_DISTINCT_SECRET_CHARS:
        return f"secret has fewer than {_MIN_DISTINCT_SECRET_CHARS} distinct characters"
    if _effective_secret_length(value) < _MIN_EFFECTIVE_SECRET_CHARS:
        return "secret is dominated by sequential runs or a repeated pattern"
    return ""


def _config_auth_enabled(config: Any) -> bool:
    security = getattr(config, "security", None)
    return bool(getattr(security, "enable_auth", False))


def _config_jwt_secret_configured(config: Any) -> bool:
    if config is not None and hasattr(config, "resolve_jwt_secret"):
        try:
            candidate = str(config.resolve_jwt_secret() or "").strip()
        except (OSError, RuntimeError):
            return False
        return bool(candidate) and not secret_strength_problem(candidate)
    security = getattr(config, "security", None)
    candidate = str(getattr(security, "jwt_secret", "") or "").strip()
    return bool(candidate) and not secret_strength_problem(candidate)


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
        checks.append({"name": name, "status": status, "severity": severity, "message": message})

    if remote:
        add("bind_host", "pass", f"{surface} binds to non-local host {bind_host}; remote safety policy applies.", "info")
        if effective_auth:
            add("auth_required", "pass", "Authentication is enabled for non-local bind.", "info")
        else:
            add("auth_required", "fail", "Authentication must be enabled for non-local bind.")
        if require_jwt_secret:
            if jwt_secret_configured:
                add("stable_jwt_secret", "pass", "A stable JWT secret is configured.", "info")
            else:
                add("stable_jwt_secret", "fail", "A stable JWT secret is required for authenticated non-local gateway bind.")
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
    lines = [f"BotBoy remote readiness: {status}", f"Surface: {report.get('surface')}", f"Bind: {report.get('host')}:{report.get('port')}"]
    for check in report.get("checks", []):
        label = str(check.get("status", "")).upper()
        lines.append(f"- {label} {check.get('name')}: {check.get('message')}")
    return "\n".join(lines)


def validate_remote_gateway_readiness(config: Any, *, host: str = "", port: int = DEFAULT_GATEWAY_PORT, surface: str = "gateway") -> dict[str, Any]:
    report = build_remote_readiness_report(config, host=host, port=port, surface=surface, require_jwt_secret=True)
    if not report["ok"]:
        raise RuntimeError(format_remote_readiness_report(report))
    return report


def validate_safe_bind(host: str, *, auth_enabled: bool = False, surface: str = "gateway", port: int = DEFAULT_GATEWAY_PORT) -> dict[str, Any]:
    """Compatibility guard for surfaces that do not use BotBoyConfig."""
    report = build_remote_readiness_report(None, host=host, port=port, surface=surface, auth_enabled=auth_enabled, require_jwt_secret=(surface != "mcp"))
    if not report["ok"]:
        raise RuntimeError(format_remote_readiness_report(report))
    return report
