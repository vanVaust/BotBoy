from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tarfile
import unittest
import uuid
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _candidate_build_pythons() -> list[Path]:
    candidates = [
        ROOT / ".venv-build" / "Scripts" / "python.exe",
        Path(sys.executable),
    ]
    result: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.exists() and resolved not in result:
            result.append(resolved)
    return result


def _select_build_python() -> Path:
    for candidate in _candidate_build_pythons():
        probe = subprocess.run(
            [str(candidate), "-c", "import build; print('ok')"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode == 0:
            return candidate
    raise unittest.SkipTest("No Python interpreter with the 'build' module is available for release acceptance.")


def _select_bootstrap_python(preferred: Path | None = None) -> Path:
    candidates: list[Path] = []
    if preferred is not None:
        candidates.append(preferred)
    candidates.extend([Path(sys.executable), ROOT / ".venv-build" / "Scripts" / "python.exe"])
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if not resolved.exists():
            continue
        probe = subprocess.run(
            [str(resolved), "-m", "pip", "--version"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode == 0:
            return resolved
    raise unittest.SkipTest("No Python interpreter with pip is available for clean-install acceptance.")


class ReleaseBuildInstallAcceptanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.build_python = _select_build_python()
        cls.bootstrap_python = _select_bootstrap_python(preferred=cls.build_python)
        cls.acceptance_root = (ROOT / ".botboy-runtime" / "release-acceptance" / uuid.uuid4().hex[:8]).resolve()
        cls.acceptance_temp_root = (cls.acceptance_root / "tmp").resolve()
        cls.dist_dir = cls.acceptance_root / "dist"
        shutil.rmtree(cls.acceptance_root, ignore_errors=True)
        cls.dist_dir.mkdir(parents=True, exist_ok=True)
        cls.acceptance_env = os.environ.copy()
        cls.acceptance_env["BOTBOY_ACCEPTANCE_TEMP_ROOT"] = str(cls.acceptance_temp_root)

        cls.full_result = subprocess.run(
            [
                str(cls.build_python),
                "-m",
                "botboy.release_acceptance",
                "full",
                "--python",
                str(cls.build_python),
                "--bootstrap-python",
                str(cls.bootstrap_python),
                "--outdir",
                str(cls.dist_dir),
            ],
            cwd=ROOT,
            env=cls.acceptance_env,
            capture_output=True,
            text=True,
            check=False,
        )
        wheels = sorted(cls.dist_dir.glob("botboy-*.whl"), key=lambda path: path.stat().st_mtime)
        sdists = sorted(cls.dist_dir.glob("botboy-*.tar.gz"), key=lambda path: path.stat().st_mtime)
        cls.wheel_path = wheels[-1] if wheels else None
        cls.sdist_path = sdists[-1] if sdists else None

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(getattr(cls, "acceptance_root", ROOT / ".botboy-runtime" / "release-acceptance"), ignore_errors=True)

    def test_build_produces_sdist_and_wheel(self) -> None:
        self.assertEqual(self.full_result.returncode, 0, self.full_result.stdout + "\n" + self.full_result.stderr)
        self.assertIsNotNone(self.sdist_path)
        self.assertIsNotNone(self.wheel_path)
        self.assertTrue(self.sdist_path.exists())
        self.assertTrue(self.wheel_path.exists())

    def test_clean_install_acceptance_passes(self) -> None:
        self.assertEqual(self.full_result.returncode, 0, self.full_result.stdout + "\n" + self.full_result.stderr)
        self.assertIn("Built and verified release artifacts", self.full_result.stdout)

    def test_acceptance_temp_and_dist_roots_are_disjoint(self) -> None:
        self.assertEqual(self.full_result.returncode, 0, self.full_result.stdout + "\n" + self.full_result.stderr)
        self.assertNotEqual(self.acceptance_temp_root, self.dist_dir)
        with self.assertRaises(ValueError):
            self.dist_dir.relative_to(self.acceptance_temp_root)
        with self.assertRaises(ValueError):
            self.acceptance_temp_root.relative_to(self.dist_dir)

    def test_sdist_excludes_transient_workspace_roots(self) -> None:
        self.assertEqual(self.full_result.returncode, 0, self.full_result.stdout + "\n" + self.full_result.stderr)
        self.assertIsNotNone(self.sdist_path)
        with tarfile.open(self.sdist_path, "r:gz") as archive:
            names = [name.replace("\\", "/").strip("/") for name in archive.getnames()]
        members = ["/".join(name.split("/")[1:]) for name in names if "/" in name]
        forbidden_prefixes = (
            ".botboy-runtime/",
            ".botboy-mcp-runtime/",
            ".venv-build/",
            ".release-build-venv/",
            "dist/",
            "build/",
            "__pycache__/",
        )
        self.assertFalse(
            any(member.startswith(prefix) for prefix in forbidden_prefixes for member in members),
            "sdist contains transient workspace roots",
        )
        self.assertFalse(any(member.split("/", 1)[0].startswith("tmp") for member in members))

    def test_wheel_excludes_source_tree_only_roots(self) -> None:
        self.assertEqual(self.full_result.returncode, 0, self.full_result.stdout + "\n" + self.full_result.stderr)
        self.assertIsNotNone(self.wheel_path)
        with zipfile.ZipFile(self.wheel_path) as archive:
            names = [name.replace("\\", "/").strip("/") for name in archive.namelist()]
        self.assertFalse(any(name.startswith("skills/") for name in names))
        self.assertFalse(any(name.startswith("examples/skills/") for name in names))
        self.assertFalse(any(name.startswith("tests/") for name in names))
        self.assertFalse(any("/__pycache__/" in f"/{name}/" for name in names))
