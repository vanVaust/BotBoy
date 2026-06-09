from __future__ import annotations

import unittest
from unittest.mock import patch

from botboy.cli_dispatch import handle_runtime_command
from botboy.cli_support import parse_args, resolve_exec_command


class _FakeBot:
    def __init__(self) -> None:
        self.commands: list[str] = []

    async def process_command(self, command: str, **kwargs):
        self.commands.append(command)
        return {"success": True, "output": "ok"}


class CliSupportTest(unittest.TestCase):
    def test_parse_worker_command_and_resolve_exec_command(self) -> None:
        args = parse_args(["worker", "node", "register", "node-1", "planner"])
        self.assertEqual(args.command, "worker")
        self.assertEqual(args.worker_cmd, ["node", "register", "node-1", "planner"])
        self.assertEqual(resolve_exec_command(args), "worker node register node-1 planner")

    def test_handle_runtime_command_executes_worker_command(self) -> None:
        args = parse_args(["worker", "node", "list"])
        bot = _FakeBot()
        with patch("builtins.print") as print_mock:
            exit_code = handle_runtime_command(
                bot,
                args,
                interactive=lambda _bot: None,
                serve=lambda _bot, host, port: None,
            )
        self.assertEqual(exit_code, 0)
        self.assertEqual(bot.commands, ["worker node list"])
        self.assertTrue(print_mock.called)


if __name__ == "__main__":
    unittest.main()
