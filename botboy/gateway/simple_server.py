"""
SimpleHTTPServer - stdlib-only HTTP/1.1 server for BotBoy.

Provides the same REST API as the FastAPI gateway but requires
zero external dependencies. Used as the automatic fallback when
FastAPI/uvicorn is not installed.
"""
from __future__ import annotations

import json
import secrets
from http.server import BaseHTTPRequestHandler
from typing import Optional
from urllib.parse import parse_qs, urlparse

from botboy.gateway.security import default_bind_host, origin_allowed, validate_remote_gateway_readiness
from botboy.gateway.secrets import PrincipalStore, SecretStore
from botboy.gateway.simple_handler_surface import SimpleHandlerSurface
from botboy.gateway.simple_routing import (
    resolve_delete_route,
    resolve_get_route,
    resolve_post_route,
)
from botboy.gateway.simple_server_runtime import create_simple_server_runtime
from botboy.gateway.simple_server_support import (
    SimpleServerSupport,
    build_simple_handler_state,
)
from botboy.gateway.simple_static_handlers import serve_static as shared_serve_static


class SimpleAPIHandler(SimpleHandlerSurface, BaseHTTPRequestHandler):
    """HTTP request handler for BotBoy stdlib server."""

    botboy = None   # set by SimpleHTTPServer.start()
    web_dir = None  # path to web/ directory
    auth = None
    rate_limiter = None
    principal_store = None
    principal_store_factory = None
    credential_store = None
    credential_store_factory = None
    security = None
    auth_enabled = False
    request_id = ""
    protocol_version = "HTTP/1.1"
    server_host = default_bind_host()
    server_port = 8765

    def log_message(self, fmt: str, *args) -> None:
        pass

    def do_GET(self) -> None:
        self._assign_request_id()
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        params = parse_qs(parsed.query)
        handler_name, route_arg = resolve_get_route(path)
        if not handler_name:
            shared_serve_static(self, parsed.path)
            return
        handler = getattr(self, handler_name)
        if route_arg is None:
            handler(params)
            return
        handler(route_arg)

    def _verify_csrf(self) -> bool:
        from botboy.gateway.csrf import CSRF_COOKIE_NAME, CSRF_HEADER_NAME, verify_csrf_token
        import http.cookies
        cookie_header = self.headers.get("Cookie") or ""
        cookies = http.cookies.SimpleCookie(cookie_header)
        cookie_token = cookies[CSRF_COOKIE_NAME].value if CSRF_COOKIE_NAME in cookies else None
        
        if cookie_token:
            header_token = self.headers.get(CSRF_HEADER_NAME) or self.headers.get(CSRF_HEADER_NAME.lower())
            if not verify_csrf_token(header_token, cookie_token):
                self._json({"error": "CSRF token validation failed"}, 403)
                return False
        return True

    def do_POST(self) -> None:
        self._assign_request_id()
        if not self._verify_csrf():
            return
            
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length > 0 else b"{}"
            payload = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            self._json({"error": "Invalid JSON body"}, 400)
            return

        handler_name, route_arg = resolve_post_route(path)
        if not handler_name:
            self._json({"error": "Not found"}, 404)
            return
        handler = getattr(self, handler_name)
        if route_arg is None:
            handler(payload)
            return
        handler(route_arg, payload)

    def do_DELETE(self) -> None:
        self._assign_request_id()
        if not self._verify_csrf():
            return
            
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        handler_name, route_arg = resolve_delete_route(path)
        if handler_name and route_arg is not None:
            getattr(self, handler_name)(route_arg)
            return
        self._json({"error": "Not found"}, 404)

    def do_OPTIONS(self) -> None:
        self._assign_request_id()
        self.send_response(200)
        self._cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _cors_headers(self) -> None:
        origin = self.headers.get("Origin") or self.headers.get("origin") or ""
        if origin_allowed(origin, host=self.server_host, port=self.server_port):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-BotBoy-Approval, X-Request-ID")

    def _assign_request_id(self) -> None:
        self.request_id = (
            self.headers.get("X-Request-ID")
            or self.headers.get("x-request-id")
            or f"req-{secrets.token_hex(8)}"
        )

    def _write_body(self, body: bytes) -> None:
        self.close_connection = True
        try:
            self.wfile.write(body)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
            pass

    def _bytes(
        self,
        body: bytes,
        *,
        content_type: str,
        status: int = 200,
        headers: Optional[dict] = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        if self.request_id:
            self.send_header("X-Request-ID", self.request_id)
        if headers:
            for key, value in headers.items():
                self.send_header(key, value)
        self._cors_headers()
        self.end_headers()
        self._write_body(body)

    def _json(self, data: dict, status: int = 200, headers: Optional[dict] = None) -> None:
        self._bytes(
            json.dumps(data, default=str).encode(),
            content_type="application/json",
            status=status,
            headers=headers,
        )

    def _text(
        self,
        text: str,
        content_type: str = "text/plain",
        status: int = 200,
        headers: Optional[dict] = None,
    ) -> None:
        self._bytes(text.encode(), content_type=content_type, status=status, headers=headers)

    def _support(self) -> SimpleServerSupport:
        return SimpleServerSupport(self)

class SimpleHTTPServer:
    """Standalone HTTP server wrapping BotBoy (no external deps)."""

    def __init__(self, bot, host: str = default_bind_host(), port: int = 8765) -> None:
        self.bot = bot
        self.host = host
        self.port = port
        validate_remote_gateway_readiness(getattr(bot, "config", None), host=self.host, port=self.port, surface="gateway")
        self._runtime = create_simple_server_runtime(
            SimpleAPIHandler,
            host=self.host,
            port=self.port,
        )
        self._runtime.configure_handler_state(
            build_simple_handler_state(
                bot,
                host=self.host,
                port=self.port,
                secret_store_cls=SecretStore,
                principal_store_cls=PrincipalStore,
            )
        )

    @property
    def _server(self):
        return self._runtime.server

    @_server.setter
    def _server(self, value) -> None:
        self._runtime.server = value

    @property
    def _thread(self):
        return self._runtime.thread

    @_thread.setter
    def _thread(self, value) -> None:
        self._runtime.thread = value

    def _close_handler_stores(self) -> None:
        self._runtime.close_handler_stores()

    def _reset_handler_state(self) -> None:
        self._runtime.reset_handler_state()

    def start(self, blocking: bool = True) -> None:
        self._runtime.start(blocking=blocking)
        self.port = self._runtime.port

    def stop(self) -> None:
        self._runtime.stop()

