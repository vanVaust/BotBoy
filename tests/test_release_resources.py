from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from botboy.agent_skills import AgentSkillLibrary
from botboy.resources import (
    bundled_assets_dir,
    bundled_eval_manifest_path,
    bundled_eval_seed_path,
    bundled_example_skills_dir,
    bundled_mcp_server_path,
    bundled_skills_dir,
    skill_registry_path,
    status_snapshot_path,
)


class ReleaseResourcesTest(unittest.TestCase):
    def test_bundled_assets_exist(self) -> None:
        self.assertEqual(bundled_assets_dir().name, "bundled")
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
            raw_path = str(item.get("path", ""))
            self.assertFalse(Path(raw_path).is_absolute(), raw_path)

        library = AgentSkillLibrary.load()
        first = library.entries[0]
        self.assertTrue(Path(first.resolved_path).exists())

    def test_status_snapshot_is_local_and_reproducible(self) -> None:
        snapshot = json.loads(status_snapshot_path().read_text(encoding="utf-8"))
        self.assertEqual(snapshot["workspace"], "botboi_finished")
        self.assertTrue(snapshot["tests"]["available"])
        self.assertEqual(snapshot["tests"]["command"], "python -m botboy.release_smoke -v")
        self.assertEqual(snapshot["tests"]["scope"], "release_smoke")
        self.assertEqual(snapshot["tests"]["ran"], 6)
        self.assertEqual(snapshot["evals"]["default_manifest"], "botboy/data/evals/release_smoke_manifest.json")
        self.assertEqual(snapshot["evals"]["command"], "python -m botboy evals --summary")
        self.assertEqual(snapshot["gateway_modes"]["stdlib"], "verified_release_live")
        self.assertEqual(snapshot["gateway_modes"]["fastapi"], "verified_release_live")
        self.assertIn("v5_replay_diff_persistence_and_dashboard_surface", snapshot["completed_capabilities"])
        self.assertNotIn("v3_replay_diff_persistence_and_surface", snapshot["open_priorities"])

    def test_packaging_config_mentions_runtime_assets(self) -> None:
        setup_text = Path("setup.py").read_text(encoding="utf-8")
        manifest_text = Path("MANIFEST.in").read_text(encoding="utf-8")

        self.assertRegex(setup_text, r'py_modules=\["botboy_mcp_server"\]')
        self.assertNotIn("data_files=", setup_text)
        self.assertIn('_collect_package_data("bundled")', setup_text)

        self.assertIn("include botboy_mcp_server.py", manifest_text)
        self.assertIn("prune botboy.egg-info", manifest_text)
        self.assertIn("prune dist", manifest_text)
        self.assertIn("recursive-include botboy/bundled *", manifest_text)
        self.assertIn("recursive-include skills *", manifest_text)
        self.assertIn("recursive-include examples/skills *", manifest_text)
        self.assertIn("recursive-include tests *.py", manifest_text)
