from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from http.server import HTTPServer
from typing import Any

from botboy.gateway.simple_server_support import (
    apply_simple_handler_state,
    close_simple_handler_stores,
    reset_simple_handler_state,
)


class SimpleServerRuntime:
    def __init__(
        self,
        *,
        handler_cls,
        host: str,
        port: int,
        server_factory=HTTPServer,
        thread_name: str = "botboy-http",
    ) -> None:
        self.handler_cls = handler_cls
        self.host = host
        self.port = port
        self.server_factory = server_factory
        self.thread_name = thread_name
        self.server: Any | None = None
        self.thread: threading.Thread | None = None

    def configure_handler_state(self, state: dict) -> None:
        apply_simple_handler_state(self.handler_cls, state)

    def close_handler_stores(self) -> None:
        close_simple_handler_stores(self.handler_cls)

    def reset_handler_state(self) -> None:
        reset_simple_handler_state(self.handler_cls)

    def close_server(self) -> None:
        if self.server is None:
            return
        try:
            self.server.server_close()
        except OSError:
            pass

    def run_async(self, coro, *, timeout_s: int = 30):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result(timeout=timeout_s)

    def start(self, *, blocking: bool = True) -> None:
        if self.server is not None:
            return
        server = self.server_factory((self.host, self.port), self.handler_cls)
        self.server = server
        self.port = server.server_address[1]
        self.handler_cls.server_port = self.port

        if blocking:
            print(f"[BotBoy] Serving on http://{self.host}:{self.port} (stdlib)")
            try:
                server.serve_forever()
            finally:
                self.stop()
            return

        def _serve_loop() -> None:
            try:
                server.serve_forever()
            finally:
                self.close_server()
                if self.server is server:
                    self.server = None
                if self.thread is threading.current_thread():
                    self.thread = None
                self.close_handler_stores()
                self.reset_handler_state()

        self.thread = threading.Thread(target=_serve_loop, daemon=True, name=self.thread_name)
        self.thread.start()

    def stop(self) -> None:
        server = self.server
        thread = self.thread
        if server is not None:
            self.server = None
            if thread and thread.is_alive():
                try:
                    server.shutdown()
                except OSError:
                    pass
            try:
                server.server_close()
            except OSError:
                pass
        if thread:
            thread.join(timeout=5)
            if not thread.is_alive():
                self.thread = None
        self.close_handler_stores()
        if self.server is None and (self.thread is None or not self.thread.is_alive()):
            self.reset_handler_state()


def create_simple_server_runtime(
    handler_cls,
    *,
    host: str,
    port: int,
    server_factory=HTTPServer,
    thread_name: str = "botboy-http",
) -> SimpleServerRuntime:
    return SimpleServerRuntime(
        handler_cls=handler_cls,
        host=host,
        port=port,
        server_factory=server_factory,
        thread_name=thread_name,
    )
