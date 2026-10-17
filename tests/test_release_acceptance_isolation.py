"""Acceptance checks must exercise declared, isolated runtime dependencies."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from botboy import release_acceptance


class ReleaseAcceptanceIsolationTests(unittest.TestCase):
    def test_python_venv_symlink_is_preserved(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root) / "base-python"
            base.touch()
            selector = Path(root) / "venv" / "bin" / "python"
            selector.parent.mkdir(parents=True)
            try:
                selector.symlink_to(base)
            except OSError:
                self.skipTest("Creating symlinks requires additional Windows privileges")
            self.assertEqual(release_acceptance._resolve_executable(str(selector)), str(selector))
            self.assertNotEqual(release_acceptance._resolve_executable(str(selector)), str(base))

    def test_artifacts_cannot_overlap_acceptance_temp_root(self):
        with tempfile.TemporaryDirectory() as root:
            temp_root = Path(root) / "temp"
            with patch.object(release_acceptance, "RUNTIME_TEMP_ROOT", temp_root):
                with self.assertRaisesRegex(ValueError, "disjoint"):
                    release_acceptance.build_release_artifacts(python_executable="unused", outdir=temp_root / "dist")
                with self.assertRaisesRegex(ValueError, "disjoint"):
                    release_acceptance.build_release_artifacts(python_executable="unused", outdir=Path(root))

    def test_subprocess_environment_cannot_inherit_source_imports(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"PYTHONPATH": "source-tree", "PYTHONHOME": "foreign-python"}):
            env = release_acceptance._command_env(Path(root))
            self.assertNotIn("PYTHONPATH", env)
            self.assertNotIn("PYTHONHOME", env)
