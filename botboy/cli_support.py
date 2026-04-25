"""Support helpers for BotBoy's CLI surface."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence

from botboy.gateway.security import default_bind_host
from botboy.resources import runtime_home_dir
from botboy.worker_daemon import add_cli_arguments as add_worker_daemon_arguments


def build_parser() -> argparse.ArgumentParser:
    """Create the canonical BotBoy CLI parser."""
    parser = argparse.ArgumentParser(
        prog="botboy",
        description="BotBoy AI Agent Framework v0.6.0-dev",
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("start", help="Start interactive REPL")

    serve_p = subparsers.add_parser("serve", help="Start HTTP API server")
    serve_p.add_argument("--host", default=default_bind_host())
    serve_p.add_argument("--port", type=int, default=8765)

    exec_p = subparsers.add_parser("exec", help="Execute a single command")
    exec_p.add_argument("cmd", nargs="+", help="Command to execute")

    task_p = subparsers.add_parser("task", help="Task lifecycle commands")
    task_p.add_argument("task_cmd", nargs="+", help="Task sub-command to execute")

    worker_p = subparsers.add_parser("worker", help="Worker and worker-node commands")
    worker_p.add_argument("worker_cmd", nargs="+", help="Worker sub-command to execute")

    security_p = subparsers.add_parser("security", help="Security and remote-readiness commands")
    security_p.add_argument("security_cmd", nargs="+", help="Security sub-command to execute")

    worker_daemon_p = subparsers.add_parser("worker-daemon", help="Run the external worker daemon")
    add_worker_daemon_arguments(worker_daemon_p)

    eval_p = subparsers.add_parser("evals", help="Run eval/replay baseline")
    eval_p.add_argument("--wave", default="", help="Optional eval wave alias, e.g. 1, 2, wave_1, wave_2")
    eval_p.add_argument("--manifest", default="", help="Optional path to an eval manifest JSON")
    eval_p.add_argument("--seed", "--replay", dest="seed", default="", help="Optional path to replay seed JSONL")
    eval_p.add_argument("--format", choices=("text", "json", "summary"), default=None, help="Output format for stdout")
    eval_p.add_argument("--json", action="store_true", help="Shortcut for --format json")
    eval_p.add_argument("--text", action="store_true", help="Shortcut for --format text")
    eval_p.add_argument("--summary", action="store_true", help="Shortcut for --format summary")
    eval_p.add_argument("--report-json", default="", help="Optional path for a JSON report artifact")

    subparsers.add_parser("init", help="Create default config file")
    subparsers.add_parser("test", help="Run integration tests")
    subparsers.add_parser("version", help="Print version")
    subparsers.add_parser("modules", help="List available modules")
    return parser


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """Parse CLI arguments for BotBoy."""
    parser = build_parser()
    return parser.parse_args(list(argv) if argv is not None else None)


def build_eval_argv(args: argparse.Namespace) -> list[str]:
    """Translate CLI eval flags into the eval module argv."""
    eval_argv: list[str] = []
    if args.manifest:
        eval_argv.extend(["--manifest", args.manifest])
    if args.seed:
        eval_argv.extend(["--seed", args.seed])
    if args.wave:
        eval_argv.extend(["--wave", args.wave])
    if args.format:
        eval_argv.extend(["--format", args.format])
    if args.json:
        eval_argv.append("--json")
    if args.text:
        eval_argv.append("--text")
    if args.summary:
        eval_argv.append("--summary")
    if args.report_json:
        eval_argv.extend(["--report-json", args.report_json])
    return eval_argv


def resolve_init_config_path() -> Path:
    """Resolve the config path used by `botboy init`."""
    configured = str(os.getenv("BOTBOY_CONFIG", "")).strip()
    return Path(configured).expanduser() if configured else runtime_home_dir() / "config.yaml"


def format_modules_output(modules: dict[str, bool]) -> str:
    """Render optional module availability for CLI output."""
    lines = []
    for name, available in modules.items():
        status = "yes" if available else "no"
        lines.append(f"  {status} {name}")
    return "\n".join(lines)


def resolve_exec_command(args: argparse.Namespace) -> str:
    """Resolve the effective command string for exec/task modes."""
    if args.command == "task":
        return "task " + " ".join(args.task_cmd)
    if args.command == "worker":
        return "worker " + " ".join(args.worker_cmd)
    if args.command == "security":
        return "security " + " ".join(args.security_cmd)
    return " ".join(args.cmd)


def run_release_smoke(cwd: Path) -> int:
    """Execute the installable release smoke suite."""
    result = subprocess.run(
        [sys.executable, "-m", "botboy.release_smoke", "-v"],
        cwd=cwd,
    )
    return int(result.returncode)
