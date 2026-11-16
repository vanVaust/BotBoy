from __future__ import annotations

import unittest

from botboy.command_execution_service import CommandExecutionService


class _Skills:
    @staticmethod
    def get_contract_for_command(_command):
        return None


class _Bot:
    def __init__(self, skills=True):
        self.skills = _Skills() if skills else None


class DestructiveCommandGateTests(unittest.TestCase):
    def test_classification_is_exact_but_case_and_space_insensitive(self):
        classify = CommandExecutionService.is_destructive_command
        for command in ("history clear", " HISTORY   CLEAR ", "forget", "Forget everything"):
            self.assertTrue(classify(command), command)
        for command in ("history", "history stats", "history clearance", "forgetful", "remember forget"):
            self.assertFalse(classify(command), command)

    def test_gateway_and_worker_contexts_need_server_approval(self):
        for with_skills in (False, True):
            service = CommandExecutionService(_Bot(skills=with_skills))
            for source in ("fastapi", "stdlib", "worker_handoff"):
                context = {"granted": True, "explicit": True, "source": source, "reason": "payload"}
                self.assertTrue(service._approval_required("history clear", context))
                self.assertTrue(service._approval_required("forget everything", context))
            self.assertTrue(service._approval_required("history clear"))

    def test_local_cli_default_remains_available(self):
        service = CommandExecutionService(_Bot())
        self.assertFalse(service._approval_required("history clear", service._default_approval_context()))
        spoofed = {"source": "local", "reason": "payload", "granted": True}
        self.assertTrue(service._approval_required("history clear", spoofed))
        self.assertFalse(service._approval_required("status", {"source": "fastapi"}))


if __name__ == "__main__":
    unittest.main()
