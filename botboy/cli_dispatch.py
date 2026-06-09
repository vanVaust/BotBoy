"""Dispatch helpers for the BotBoy CLI surface."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from botboy.cli_support import (
    build_eval_argv,
    format_modules_output,
    resolve_exec_command,
    resolve_init_config_path,
    run_release_smoke,
)

if TYPE_CHECKING:
    import argparse

    from botboy.__main__ import BotBoy


def handle_static_command(
    args: "argparse.Namespace",
    *,
    version: str,
    available_modules: Callable[[], dict[str, bool]],
) -> Optional[int]:
    """Handle commands that do not require a live BotBoy instance."""
    if args.command == "version":
        print(f"BotBoy v{version}")
        return 0

    if args.command == "modules":
        print(format_modules_output(available_modules()))
        return 0

    if args.command == "init":
        config_path = resolve_init_config_path()
        config_path.parent.mkdir(parents=True, exist_ok=True)
        if not config_path.exists():
            config_path.write_text("# BotBoy configuration\nbotboy:\n  version: '0.6.0-dev'\n")
            print(f"Config created: {config_path}")
        else:
            print(f"Config already exists: {config_path}")
        return 0

    if args.command == "evals":
        from botboy import evals as eval_module

        return int(eval_module.main(build_eval_argv(args)))

    if args.command == "test":
        return run_release_smoke(Path(__file__).resolve().parent.parent)

    return None


def handle_runtime_command(
    bot: "BotBoy",
    args: "argparse.Namespace",
    *,
    interactive: Callable[["BotBoy"], object],
    serve: Callable[["BotBoy", str, int], None],
) -> int:
    """Handle commands that require an initialized BotBoy instance."""
    if args.command == "serve":
        serve(bot, host=args.host, port=args.port)
        return 0

    if args.command in {"exec", "task", "worker"}:
        command = resolve_exec_command(args)
        result = asyncio.run(bot.process_command(command, principal="local-cli"))
        print(result.get("output", ""))
        return 0 if result.get("success") else 1

    asyncio.run(interactive(bot))
    return 0


def exit_with(code: int) -> None:
    """Terminate the process with a normalized integer status."""
    raise SystemExit(int(code))
