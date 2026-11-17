from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from botboy.skills.manager import BuiltinSkills


def search(pattern: str, home: Path) -> dict:
    with patch.object(Path, "home", return_value=home):
        return asyncio.run(BuiltinSkills.file_search(f"file search {pattern}"))


class FileSearchConfinementTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        self.home = root / "home"
        self.outside = root / "outside"
        (self.home / "docs").mkdir(parents=True)
        self.outside.mkdir()
        (self.home / "top.txt").write_text("inside")
        (self.home / "docs" / "notes.txt").write_text("inside")
        (self.outside / "secret.txt").write_text("outside")

    def test_glob_search_remains_recursive_but_confined(self) -> None:
        for pattern in ("*.txt", "**/*.txt"):
            result = search(pattern, self.home)
            self.assertTrue(result["success"], result)
            names = {Path(p).name for p in result["data"]["matches"]}
            self.assertEqual(names, {"top.txt", "notes.txt"}, pattern)
        result = search("docs/*.txt", self.home)
        self.assertEqual([Path(p).name for p in result["data"]["matches"]], ["notes.txt"])

    def test_previous_dot_stripping_bypass_cannot_escape(self) -> None:
        # A component of dots is a hidden name and is refused before any walk.
        result = search("....//*", self.home)
        self.assertFalse(result["success"], result)
        self.assertIn("rejected", result["output"])

    def test_absolute_parent_home_drive_and_null_are_rejected(self) -> None:
        patterns = ("/etc/*", "../outside/*", "docs/../../outside/*", "~/*", "C:\\Windows\\*", "..\\outside\\*", "\x00", "a" * 300)
        for pattern in patterns:
            with self.subTest(pattern=pattern):
                result = search(pattern, self.home)
                self.assertFalse(result["success"])
                self.assertIn("rejected", result["output"])

    @unittest.skipIf(os.name == "nt", "Symlink permissions vary on Windows")
    def test_does_not_follow_outside_symlinks(self) -> None:
        (self.home / "link.txt").symlink_to(self.outside / "secret.txt")
        (self.home / "linked_dir").symlink_to(self.outside, target_is_directory=True)
        result = search("*.txt", self.home)
        names = {Path(p).name for p in result["data"]["matches"]}
        self.assertNotIn("secret.txt", names)
        self.assertNotIn("link.txt", names)
        for match in result["data"]["matches"]:
            self.assertTrue(Path(match).resolve().is_relative_to(self.home))

    @unittest.skipUnless(os.name == "nt", "NTFS junctions exist only on Windows")
    def test_never_enters_ntfs_junction_on_supported_python_versions(self) -> None:
        junction = self.home / "junction"
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(self.outside)],
            capture_output=True,
            check=True,
            text=True,
        )
        original_scandir = os.scandir

        def guarded_scandir(path):
            if Path(path) == junction:
                raise AssertionError("file search traversed an NTFS junction")
            return original_scandir(path)

        with patch("os.scandir", side_effect=guarded_scandir):
            result = search("*.txt", self.home)
        self.assertTrue(result["success"], result)
        self.assertEqual(
            {Path(value).name for value in result["data"]["matches"]},
            {"top.txt", "notes.txt"},
        )

    def test_result_limit_and_hidden_filenames(self) -> None:
        for idx in range(40):
            (self.home / f"file{idx}.log").write_text("inside")
        (self.home / ".secret.log").write_text("inside")
        result = search("*.log", self.home)
        self.assertEqual(len(result["data"]["matches"]), BuiltinSkills._FILE_SEARCH_MAX_RESULTS)
        self.assertNotIn(".secret.log", [Path(p).name for p in result["data"]["matches"]])
        self.assertFalse(result["data"]["complete"])
        self.assertEqual(result["data"]["stop_reason"], "result_limit")

    def test_hidden_paths_are_never_searchable(self) -> None:
        (self.home / ".ssh").mkdir()
        (self.home / ".ssh" / "id_ed25519").write_text("key")
        (self.home / ".secret.log").write_text("inside")
        for pattern in (".*.log", ".ssh/*", "docs/.hidden", ".\\.ssh\\*"):
            with self.subTest(pattern=pattern):
                result = search(pattern, self.home)
                self.assertFalse(result["success"], result)
                self.assertIn("Hidden", result["output"])
        for pattern in ("*", "**/*", "id_*", "*ed25519"):
            with self.subTest(pattern=pattern):
                names = {Path(p).name for p in search(pattern, self.home)["data"]["matches"]}
                self.assertNotIn("id_ed25519", names)
                self.assertNotIn(".secret.log", names)

    def test_budgets_stop_walk_and_report_incomplete(self) -> None:
        for idx in range(30):
            (self.home / "docs" / f"bulk{idx}.dat").write_text("x")
        with patch.object(BuiltinSkills, "_FILE_SEARCH_MAX_ENTRIES", 10):
            result = search("nothing-matches-*.xyz", self.home)
        self.assertTrue(result["success"], result)
        self.assertEqual(result["data"]["stop_reason"], "entry_budget")
        self.assertFalse(result["data"]["complete"])
        self.assertIn("incomplete", result["output"])
        complete = search("nothing-matches-*.xyz", self.home)
        self.assertTrue(complete["data"]["complete"])


if __name__ == "__main__":
    unittest.main()
