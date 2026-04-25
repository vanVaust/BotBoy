from __future__ import annotations

import asyncio
import unittest
import uuid
from pathlib import Path

from botboy.__main__ import BotBoy
from botboy.core.config import BotBoyConfig


class ReleaseRuntimeTest(unittest.TestCase):
    def _make_bot(self) -> BotBoy:
        temp_root = (Path(__file__).resolve().parents[1] / ".botboy-runtime" / f"{self._testMethodName}").resolve()
        temp_root.mkdir(parents=True, exist_ok=True)
        config = BotBoyConfig()
        config.memory.db_path = str(temp_root / "botboy.db")
        config.history.db_path = str(temp_root / "history.db")
        config.scheduler.db_path = str(temp_root / "scheduler.db")
        config.trace.db_path = str(temp_root / "traces.db")
        config.tasks.db_path = str(temp_root / "tasks.db")
        config.tasks.artifact_root = str(temp_root / "artifacts" / "tasks")
        config.security.principal_db_path = str(temp_root / "principals.db")
        config.security.auth_api_key_store_path = str(temp_root / "api_keys.db")
        config.security.jwt_secret_file = str(temp_root / "jwt.secret")
        config.security.enable_auth = False
        config.llm.enabled = False
        bot = BotBoy(config)
        self.assertTrue(bot.initialize())
        self.addCleanup(bot.shutdown)
        return bot

    def test_default_skill_path_loads_bundled_skills(self) -> None:
        bot = self._make_bot()
        self.assertIsNotNone(bot.skills)
        skill_names = bot.skills.list_skills()
        self.assertIn("botboy-cloud-topology-planner", skill_names)
        self.assertGreater(len(skill_names), 20)

    def test_status_and_skills_commands_work(self) -> None:
        bot = self._make_bot()
        status = asyncio.run(bot.process_command("status", principal="release.test"))
        skills = asyncio.run(bot.process_command("skills", principal="release.test"))
        help_result = asyncio.run(bot.process_command("help", principal="release.test"))
        self.assertTrue(status["success"])
        self.assertIn("Status", status["output"])
        self.assertTrue(skills["success"])
        self.assertIn("Skills:", skills["output"])
        self.assertTrue(help_result["success"])
        self.assertIn("Available Commands", help_result["output"])

    def test_history_and_task_commands_work(self) -> None:
        bot = self._make_bot()
        asyncio.run(bot.process_command("status", principal="release.test"))
        remember = asyncio.run(bot.process_command("remember release-runtime-check", principal="release.test"))
        history = asyncio.run(bot.process_command("history stats", principal="release.test"))
        tasks = asyncio.run(bot.process_command("task list", principal="release.test"))
        schedule = asyncio.run(bot.process_command("schedule list", principal="release.test"))
        self.assertTrue(remember["success"])
        self.assertTrue(history["success"])
        self.assertIn("History stats:", history["output"])
        self.assertTrue(tasks["success"])
        self.assertIn("tasks", tasks["output"].lower())
        self.assertTrue(schedule["success"])
        self.assertIn("schedule", schedule["type"])

    def test_worker_reflect_and_archetype_commands_work(self) -> None:
        bot = self._make_bot()
        worker = asyncio.run(bot.process_command("worker list", principal="release.test"))
        reflect = asyncio.run(bot.process_command("reflect stats", principal="release.test"))
        archetypes = asyncio.run(bot.process_command("archetypes stats", principal="release.test"))
        self.assertTrue(worker["success"])
        self.assertIn("Workers", worker["output"])
        self.assertTrue(reflect["success"])
        self.assertIn("Reflection archive:", reflect["output"])
        self.assertTrue(archetypes["success"])
        self.assertIn("Archetype routing stats", archetypes["output"])

    def test_worker_node_commands_work(self) -> None:
        bot = self._make_bot()
        node_id = f"node-release-{uuid.uuid4().hex[:8]}"
        queue_name = f"planner.{node_id}"
        register = asyncio.run(bot.process_command(f"worker node register {node_id} planner", principal="release.test"))
        listing = asyncio.run(bot.process_command("worker node list", principal="release.test"))
        queues = asyncio.run(bot.process_command("worker lease queues", principal="release.test"))
        lease = asyncio.run(
            bot.process_command(f"worker lease acquire {queue_name} {node_id}", principal="release.test")
        )
        lease_id = lease["data"]["lease"]["lease_id"]
        leases = asyncio.run(bot.process_command(f"worker lease list {queue_name}", principal="release.test"))
        renew = asyncio.run(bot.process_command(f"worker lease renew {lease_id}", principal="release.test"))
        release = asyncio.run(bot.process_command(f"worker lease release {lease_id} done", principal="release.test"))
        heartbeat = asyncio.run(
            bot.process_command(f"worker node heartbeat {node_id} running", principal="release.test")
        )
        drain = asyncio.run(bot.process_command(f"worker node drain {node_id} maintenance", principal="release.test"))
        status = asyncio.run(bot.process_command("status", principal="release.test"))
        self.assertTrue(register["success"])
        self.assertIn(node_id, register["output"])
        self.assertTrue(listing["success"])
        self.assertIn("Worker nodes", listing["output"])
        self.assertTrue(queues["success"])
        self.assertIn("Execution queues", queues["output"])
        self.assertTrue(lease["success"])
        self.assertIn("Queue lease acquired", lease["output"])
        self.assertTrue(leases["success"])
        self.assertIn(lease_id, leases["output"])
        self.assertTrue(renew["success"])
        self.assertIn("renewed", renew["output"])
        self.assertTrue(release["success"])
        self.assertIn("released", release["output"])
        self.assertTrue(heartbeat["success"])
        self.assertIn("heartbeat", heartbeat["output"].lower())
        self.assertTrue(drain["success"])
        self.assertIn("draining", drain["output"].lower())
        self.assertTrue(status["success"])
        self.assertIn("Worker nodes:", status["output"])
