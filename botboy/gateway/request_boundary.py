"""Browser-to-gateway trust boundary for both HTTP transports and WebSocket.

Local-only binding is not a CSRF or DNS-rebinding defence: a browser visiting
an unrelated site can still send requests to a loopback service.  These
checks deliberately run even when local authentication is disabled.

``BOTBOY_ALLOWED_HOSTS`` adds comma-separated hostnames to the accepted Host
headers.  For non-local binds, hosts are unrestricted unless configured;
those binds separately require authentication.  A wildcard never disables
the loopback Host check.  ``BOTBOY_ENABLE_DOCS=1`` enables FastAPI API docs.
"""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlsplit

from botboy.gateway.security import _canonical_host, is_local_bind_host, resolve_cors_origins


_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_TRUTHY = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True)
class BoundaryDecision:
    allowed: bool
    status: int = 200
    code: str = ""
    message: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"error": self.message, "code": self.code}


_ALLOW = BoundaryDecision(True)


def docs_enabled() -> bool:
    return os.getenv("BOTBOY_ENABLE_DOCS", "").strip().lower() in _TRUTHY


def parse_host_header(value: Optional[str]) -> str:
    """Extract one syntactically valid hostname (never userinfo or a URL)."""
    raw = str(value or "").strip().lower()
    if not raw or any(ch in raw for ch in ("@", "/", "\\", "?", "#", ",")):
        return ""
    if any(ch.isspace() or ord(ch) < 32 for ch in raw):
        return ""
    if raw.startswith("["):
        end = raw.find("]")
        if end == -1:
            return ""
        try:
            ipaddress.IPv6Address(raw[1:end])
        except ValueError:
            return ""
        suffix = raw[end + 1 :]
        if suffix and not _valid_port_suffix(suffix):
            return ""
        return raw[1:end]
    if raw.count(":") > 1:
        return ""  # HTTP Host requires bracketed IPv6 literals.
    if ":" in raw:
        hostname, port = raw.rsplit(":", 1)
        if not _valid_port_suffix(":" + port):
            return ""
    else:
        hostname = raw
    if not hostname or "[" in hostname or "]" in hostname:
        return ""
    return hostname


def _valid_port_suffix(value: str) -> bool:
    digits = value[1:]
    return (
        value.startswith(":") and 1 <= len(digits) <= 5
        and digits.isascii() and digits.isdigit() and 0 < int(digits) <= 65535
    )


def allowed_hosts(bind_host: str) -> Optional[frozenset[str]]:
    """Return allowed hostnames, or None for an authenticated remote bind."""
    extras = {
        item.strip().lower()
        for item in os.getenv("BOTBOY_ALLOWED_HOSTS", "").split(",")
        if item.strip() and item.strip() != "*"
    }
    if is_local_bind_host(bind_host):
        return frozenset(_LOOPBACK_HOSTS | {_canonical_host(bind_host).lower()} | extras)
    return frozenset(extras) if extras else None


def _json_media_type(value: Optional[str]) -> bool:
    media_type = str(value or "").split(";", 1)[0].strip().lower()
    return media_type == "application/json" or (media_type.startswith("application/") and media_type.endswith("+json"))


def _valid_origin(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in {"http", "https"}:
            return False
        if not parsed.hostname or parsed.username is not None or parsed.password is not None:
            return False
        if parsed.path or parsed.query or parsed.fragment:
            return False
        # Accessing .port validates a possible numerical port.
        _ = parsed.port
        return True
    except ValueError:
        return False


_DEFAULT_PORTS = {"http": 80, "https": 443}


def _host_header_port(host_header: str) -> Optional[int]:
    raw = host_header.strip()
    tail = raw[raw.rfind("]") + 1 :] if raw.startswith("[") else raw
    if ":" not in tail:
        return None
    return int(tail.rsplit(":", 1)[1])


def _origin_matches_host(origin: str, host_header: str, host: str) -> bool:
    """Same-origin test that treats an omitted port as the scheme default.

    Browsers omit default ports in ``Origin`` (``http://localhost``) while a
    client may send ``Host: localhost:80``.  The server cannot see the scheme
    behind a TLS terminator, so a Host without port accepts the origin's own
    scheme default; an explicit Host port must equal the origin's port.
    """
    parsed = urlsplit(origin)
    if (parsed.hostname or "").lower() != host:
        return False
    origin_port = parsed.port or _DEFAULT_PORTS[parsed.scheme.lower()]
    host_port = _host_header_port(host_header)
    if host_port is None:
        return parsed.port is None
    return origin_port == host_port


def check_request_boundary(
    *,
    method: str,
    host_header: Optional[str],
    origin: Optional[str],
    content_type: Optional[str],
    has_body: bool,
    bind_host: str,
    port: int,
) -> BoundaryDecision:
    hosts = allowed_hosts(bind_host)
    host = parse_host_header(host_header)
    if hosts is not None and host not in hosts:
        return BoundaryDecision(False, 403, "host_not_allowed", "Host header not allowed")

    origin_value = str(origin or "").strip()
    if origin_value:
        if not _valid_origin(origin_value):
            return BoundaryDecision(False, 403, "origin_not_allowed", "Origin not allowed")
        same_origin = bool(host) and _origin_matches_host(origin_value, str(host_header or ""), host)
        # An explicitly configured wildcard CORS policy must NOT disable this
        # browser security boundary.  Exact dev origins remain supported.
        permitted = origin_value in resolve_cors_origins(host=bind_host, port=port)
        if not same_origin and not permitted:
            return BoundaryDecision(False, 403, "origin_not_allowed", "Origin not allowed")

    if method.upper() in _UNSAFE_METHODS and has_body and not _json_media_type(content_type):
        return BoundaryDecision(False, 415, "unsupported_media_type", "Content-Type must be application/json")
    return _ALLOW
