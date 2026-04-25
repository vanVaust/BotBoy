from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

import botboy.release_acceptance as release_acceptance
import botboy.resources as resource_paths
from botboy.tasks import TaskStore
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
        self.assertEqual(snapshot["acceptance"]["temp_root_default"], "system-temp/botboy-release-acceptance")

    def test_packaging_config_mentions_runtime_assets(self) -> None:
        setup_text = Path("setup.py").read_text(encoding="utf-8")
        manifest_text = Path("MANIFEST.in").read_text(encoding="utf-8")

        self.assertRegex(setup_text, r'py_modules=\["botboy_mcp_server"\]')
        self.assertNotIn("data_files=", setup_text)
        self.assertIn('_collect_package_data("bundled")', setup_text)
        self.assertIn("IGNORED_PACKAGE_PARTS", setup_text)
        self.assertIn("IGNORED_PACKAGE_SUFFIXES", setup_text)

        self.assertIn("include botboy_mcp_server.py", manifest_text)
        self.assertIn("prune botboy.egg-info", manifest_text)
        self.assertIn("prune dist", manifest_text)
        self.assertIn("recursive-include botboy/bundled *", manifest_text)
        self.assertIn("recursive-include skills *", manifest_text)
        self.assertIn("recursive-include examples/skills *", manifest_text)
        self.assertIn("recursive-include tests *.py", manifest_text)
        self.assertIn("global-exclude __pycache__", manifest_text)
        self.assertIn("global-exclude .DS_Store", manifest_text)

    def test_release_acceptance_uses_os_temp_default_and_disjoint_guard(self) -> None:
        expected_default = (Path(tempfile.gettempdir()) / "botboy-release-acceptance").resolve()
        self.assertEqual(release_acceptance.DEFAULT_RUNTIME_TEMP_ROOT, expected_default)
        with self.assertRaisesRegex(RuntimeError, "disjoint"):
            release_acceptance._assert_disjoint_paths(
                {
                    "a": Path(tempfile.gettempdir()) / "release-overlap",
                    "b": Path(tempfile.gettempdir()) / "release-overlap" / "nested",
                }
            )

    def test_runtime_home_temp_fallback_is_isolated_from_install_tree(self) -> None:
        install_root_resolved = resource_paths.install_root().resolve()
        temp_fallback = resource_paths._runtime_home_temp_fallback()
        self.assertFalse(str(temp_fallback).startswith(str(install_root_resolved)))

    def test_packaging_extras_match_requirements(self) -> None:
        setup_text = Path("setup.py").read_text(encoding="utf-8")

        for dependency in ("httpx>=0.26.0", "psutil>=5.9.0"):
            self.assertIn(dependency, setup_text)
        self.assertIn("pyaudio>=0.2.13", setup_text)

    def test_web_ui_escapes_bot_output_before_html_insertion(self) -> None:
        index_text = Path("botboy/web/index.html").read_text(encoding="utf-8")

        self.assertIn("const blockPlaceholders = [];", index_text)
        self.assertIn("out = escHtml(out)", index_text)
        self.assertIn("${escHtml(type)}", index_text)

    def test_ci_release_gate_uses_os_python_matrix_and_fails_skips(self) -> None:
        workflow = Path(".github/workflows/release-verification.yml").read_text(encoding="utf-8")

        self.assertIn("os: [windows-latest, ubuntu-latest]", workflow)
        self.assertIn('python-version: ["3.10", "3.11", "3.12", "3.13"]', workflow)
        self.assertIn("result.skipped", workflow)

    def test_write_artifact_rejects_path_escape(self) -> None:
        root = (Path(__file__).resolve().parents[1] / ".botboy-runtime" / self._testMethodName).resolve()
        root.mkdir(parents=True, exist_ok=True)
        store = TaskStore(
            db_path=str(root / "tasks.db"),
            artifact_root=str(root / "artifacts" / "tasks"),
        )
        self.addCleanup(store.close)
        task = store.create_task(title="artifact root", command="status", status="queued")

        with self.assertRaisesRegex(ValueError, "escapes"):
            store.write_artifact(
                task.task_id,
                category="merge_report",
                label="escape",
                filename="..\\..\\escape.json",
                content="{}",
                media_type="application/json",
            )

    def test_add_artifact_file_copies_external_sources_into_artifact_root(self) -> None:
        root = (Path(__file__).resolve().parents[1] / ".botboy-runtime" / self._testMethodName).resolve()
        root.mkdir(parents=True, exist_ok=True)
        store = TaskStore(
            db_path=str(root / "tasks.db"),
            artifact_root=str(root / "artifacts" / "tasks"),
        )
        self.addCleanup(store.close)
        task = store.create_task(title="artifact import", command="status", status="queued")
        external = root / "external.json"
        external.write_text('{"ok": true}', encoding="utf-8")

        artifact = store.add_artifact_file(
            task.task_id,
            category="merge_report",
            label="external import",
            file_path=str(external),
            media_type="application/json",
            allow_external_source=True,
        )

        self.assertTrue(Path(artifact.file_path).exists())
        self.assertTrue(str(Path(artifact.file_path).resolve()).startswith(str(store.artifact_root.resolve())))
        self.assertNotEqual(Path(artifact.file_path).resolve(), external.resolve())

    def test_link_artifacts_rejects_poisoned_external_artifact_paths(self) -> None:
        root = (Path(__file__).resolve().parents[1] / ".botboy-runtime" / self._testMethodName).resolve()
        root.mkdir(parents=True, exist_ok=True)
        store = TaskStore(
            db_path=str(root / "tasks.db"),
            artifact_root=str(root / "artifacts" / "tasks"),
        )
        self.addCleanup(store.close)
        parent = store.create_task(title="parent", command="status", status="queued")
        child = store.create_task(title="child", command="status", status="completed")
        poisoned = root / "poisoned.txt"
        poisoned.write_text("secret", encoding="utf-8")
        artifact_id = store._new_id("art")
        now = store._now()
        conn = store._get_conn()
        conn.execute(
            "INSERT INTO task_artifacts (artifact_id, task_id, category, label, file_path, media_type, size_bytes, sha256, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (
                artifact_id,
                child.task_id,
                "merge_report",
                "poisoned",
                str(poisoned.resolve()),
                "text/plain",
                len(poisoned.read_bytes()),
                "deadbeef",
                now,
            ),
        )
        conn.commit()

        with self.assertRaisesRegex(ValueError, "artifact_root"):
            store.link_artifacts_from_task(parent.task_id, child.task_id)
