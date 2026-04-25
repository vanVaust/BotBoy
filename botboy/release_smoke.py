from __future__ import annotations

import argparse
import asyncio
import json
import unittest
from pathlib import Path
from uuid import uuid4

from botboy.__main__ import BotBoy
from botboy.agent_skills import AgentSkillLibrary
from botboy.core.config import BotBoyConfig
from botboy.evals import EvalReplayRunner, default_manifest_path, default_seed_path
from botboy.resources import (
    bundled_eval_manifest_path,
    bundled_eval_seed_path,
    bundled_example_skills_dir,
    bundled_mcp_server_path,
    bundled_skills_dir,
    skill_registry_path,
    status_snapshot_path,
)


def _build_runtime_config(temp_root: Path) -> BotBoyConfig:
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
    return config


def _runtime_root(name: str) -> Path:
    root = Path(__file__).resolve().parent.parent / ".botboy-runtime" / "release-smoke" / f"{name}-{uuid4().hex[:8]}"
    root.mkdir(parents=True, exist_ok=True)
    return root


class ReleaseResourcesTest(unittest.TestCase):
    def test_bundled_assets_exist(self) -> None:
        self.assertTrue(bundled_skills_dir().exists())
        self.assertTrue(any(bundled_skills_dir().rglob("SKILL.md")))
        self.assertTrue(bundled_example_skills_dir().exists())
        self.assertTrue(any(bundled_example_skills_dir().rglob("SKILL.md")))
        self.assertTrue(bundled_mcp_server_path().exists())
        self.assertTrue(bundled_eval_manifest_path().exists())
        self.assertTrue(bundled_eval_seed_path().exists())

    def test_skill_registry_uses_relative_paths(self) -> None:
        payload = json.loads(skill_registry_path().read_text(encoding="utf-8-sig"))
        skills = payload.get("skills", [])
        self.assertTrue(skills)
        for item in skills:
            self.assertFalse(Path(str(item.get("path", ""))).is_absolute(), item.get("path", ""))
        library = AgentSkillLibrary.load()
        self.assertTrue(Path(library.entries[0].resolved_path).exists())

    def test_status_snapshot_tracks_release_smokes(self) -> None:
        snapshot = json.loads(status_snapshot_path().read_text(encoding="utf-8"))
        self.assertEqual(snapshot["workspace"], "botboi_finished")
        self.assertTrue(snapshot["tests"]["available"])
        self.assertEqual(snapshot["tests"]["command"], "python -m botboy.release_smoke -v")
        self.assertEqual(snapshot["evals"]["default_manifest"], "botboy/data/evals/release_smoke_manifest.json")


class ReleaseRuntimeTest(unittest.TestCase):
    def _make_bot(self) -> BotBoy:
        temp_root = _runtime_root(self._testMethodName)
        bot = BotBoy(_build_runtime_config(temp_root))
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
        self.assertTrue(status["success"])
        self.assertIn("Status", status["output"])
        self.assertTrue(skills["success"])
        self.assertIn("Skills:", skills["output"])


class ReleaseEvalSmokeTest(unittest.TestCase):
    def test_default_release_eval_bundle_passes(self) -> None:
        runner = EvalReplayRunner(default_manifest_path(), default_seed_path())
        result = runner.run()
        self.assertEqual(result.failed, 0, result.render())
        self.assertEqual(result.passed, result.total)
        self.assertFalse(result.integrity_issues, result.render())


def load_suite() -> unittest.TestSuite:
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite()
    for case in (ReleaseResourcesTest, ReleaseRuntimeTest, ReleaseEvalSmokeTest):
        suite.addTests(loader.loadTestsFromTestCase(case))
    return suite


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run BotBoy release smoke checks")
    parser.add_argument("-v", "--verbose", action="store_true", help="Use unittest verbosity 2")
    args = parser.parse_args(argv)
    result = unittest.TextTestRunner(verbosity=2 if args.verbose else 1).run(load_suite())
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
