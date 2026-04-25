"""CLI entrypoint and interactive shell for BotBoy."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Optional, Sequence

from botboy.cli_dispatch import exit_with, handle_runtime_command, handle_static_command
from botboy.cli_support import parse_args
from botboy.core.config import BotBoyConfig
from botboy.gateway.security import default_bind_host
from botboy.runtime import available_modules

if TYPE_CHECKING:
    from botboy.__main__ import BotBoy


async def interactive(bot: "BotBoy") -> None:
    """Interactive REPL loop."""
    print(f"\n  BotBoy v{bot.VERSION}  |  Type 'help' for commands  |  'exit' to quit\n")
    while True:
        try:
            raw = input("BotBoy> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not raw:
            continue
        if raw.lower() in ("exit", "quit", "q", "bye"):
            print("Goodbye!")
            break

        result = await bot.process_command(raw, principal="local-cli")
        output = result.get("output", "")
        if output:
            print(output)


def cmd_serve(bot: "BotBoy", host: str = default_bind_host(), port: int = 8765) -> None:
    """Start HTTP API server."""
    try:
        from botboy.gateway.server import start_server

        start_server(bot, host=host, port=port)
    except ImportError:
        try:
            from botboy.gateway.simple_server import SimpleHTTPServer

            server = SimpleHTTPServer(bot, host=host, port=port)
            print(f"[BotBoy] Serving on http://{host}:{port} (stdlib mode)")
            server.start()
        except (OSError, RuntimeError) as exc:
            print(f"[BotBoy] Server error: {exc}")


def main(argv: Optional[Sequence[str]] = None) -> None:
    from botboy.__main__ import BotBoy

    args = parse_args(argv)
    exit_code = handle_static_command(args, version=BotBoy.VERSION, available_modules=available_modules)
    if exit_code is not None:
        exit_with(exit_code)

    config = BotBoyConfig.load()
    bot = BotBoy(config)
    if not bot.initialize():
        raise SystemExit("[BotBoy] Initialisation failed. Check logs.")

    try:
        exit_with(
            handle_runtime_command(
                bot,
                args,
                interactive=interactive,
                serve=cmd_serve,
            )
        )
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        bot.shutdown()
