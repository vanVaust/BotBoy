from __future__ import annotations

import asyncio
import threading
import time
import unittest

from botboy.gateway.simple_server_runtime import (
    SimpleServerRuntime,
    create_simple_server_runtime,
)


class _Handler:
    server_port = 0
    principal_store = None
    principal_store_factory = None
    credential_store = None
    credential_store_factory = None
    security = None
    auth_enabled = False


class _FakeServer:
    def __init__(self, address, handler_cls) -> None:
        self.server_address = (address[0], 43210)
        self.handler_cls = handler_cls
        self._stop = threading.Event()
        self.closed = False

    def serve_forever(self) -> None:
        self._stop.wait(timeout=2)

    def shutdown(self) -> None:
        self._stop.set()

    def server_close(self) -> None:
        self.closed = True


class _ShutdownMustNotRunServer(_FakeServer):
    def shutdown(self) -> None:
        raise AssertionError("shutdown() should not be called when no serving thread is active")


class SimpleServerRuntimeTest(unittest.TestCase):
    def test_factory_returns_runtime(self) -> None:
        runtime = create_simple_server_runtime(_Handler, host="127.0.0.1", port=0)
        self.assertIsInstance(runtime, SimpleServerRuntime)

    def test_run_async_without_loop(self) -> None:
        runtime = create_simple_server_runtime(_Handler, host="127.0.0.1", port=0)

        async def _coro():
            return "ok"

        self.assertEqual(runtime.run_async(_coro()), "ok")

    def test_run_async_with_running_loop(self) -> None:
        runtime = create_simple_server_runtime(_Handler, host="127.0.0.1", port=0)

        async def _inner():
            return runtime.run_async(asyncio.sleep(0, result="nested"))

        self.assertEqual(asyncio.run(_inner()), "nested")

    def test_start_and_stop_non_blocking(self) -> None:
        runtime = create_simple_server_runtime(
            _Handler,
            host="127.0.0.1",
            port=0,
            server_factory=_FakeServer,
        )
        runtime.start(blocking=False)
        time.sleep(0.05)
        self.assertEqual(runtime.port, 43210)
        self.assertIsNotNone(runtime.thread)
        runtime.stop()
        self.assertTrue(runtime.thread is None or not runtime.thread.is_alive())

    def test_stop_without_running_thread_skips_shutdown(self) -> None:
        runtime = create_simple_server_runtime(
            _Handler,
            host="127.0.0.1",
            port=0,
            server_factory=_ShutdownMustNotRunServer,
        )
        runtime.server = _ShutdownMustNotRunServer(("127.0.0.1", 0), _Handler)
        runtime.thread = None

        runtime.stop()

        self.assertIsNone(runtime.server)
