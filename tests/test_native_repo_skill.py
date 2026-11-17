from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from botboy.skills.native import repo as repo_skill
from botboy.skills.native.base import NativeSkillContext
from botboy.skills.native.repo import GitError, locate_repository, parse_status

GIT = shutil.which("git")


def git(cwd: Path, *args: str) -> None:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=env)


@unittest.skipUnless(GIT, "git is not installed")
class RepoSkillTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.root = self.base / "workspace"
        self.project = self.root / "project"
        self.project.mkdir(parents=True)
        git(self.project, "init", "-q", "-b", "main")
        git(self.project, "config", "user.email", "tester@example.invalid")
        git(self.project, "config", "user.name", "Tester")
        (self.project / "a.txt").write_text("one\n", encoding="utf-8")
        git(self.project, "add", "a.txt")
        git(self.project, "commit", "-q", "-m", "first commit")
        git(self.project, "checkout", "-q", "-b", "feature")
        (self.project / "b.txt").write_text("new\n", encoding="utf-8")
        git(self.project, "add", "b.txt")
        git(self.project, "commit", "-q", "-m", "add b", "-m", "Body line explaining b.")
        (self.project / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
        (self.project / "staged.txt").write_text("staged\n", encoding="utf-8")
        git(self.project, "add", "staged.txt")
        (self.project / "untracked.txt").write_text("u\n", encoding="utf-8")
        self.marker = self.base / "PWNED"
        self.ctx = NativeSkillContext(workspace_root=self.root)

    def call(self, command: str) -> dict:
        return asyncio.run(repo_skill.run(command, self.ctx))

    def evil_repo(self, key: str, value: str) -> Path:
        evil = self.root / "evil"
        evil.mkdir()
        git(evil, "init", "-q")
        git(evil, "config", key, value)
        (evil / "f.txt").write_text("x\n", encoding="utf-8")
        return evil

    def test_status(self) -> None:
        result = self.call("repo status project")
        self.assertTrue(result["success"], result)
        data = result["data"]
        self.assertEqual(data["branch"], "feature")
        self.assertEqual([item["path"] for item in data["staged"]], ["staged.txt"])
        self.assertEqual([item["path"] for item in data["unstaged"]], ["a.txt"])
        self.assertEqual(data["untracked"], ["untracked.txt"])

    def test_status_does_not_rewrite_the_index(self) -> None:
        index = self.project / ".git" / "index"
        os.utime(self.project / "a.txt", (1, 1))
        before = index.stat().st_mtime_ns
        self.call("repo status project")
        self.assertEqual(index.stat().st_mtime_ns, before)

    def test_log_branches_show(self) -> None:
        log = self.call("repo log project -n 5")
        self.assertEqual([c["subject"] for c in log["data"]["commits"]], ["add b", "first commit"])
        main_only = self.call("repo log project --rev main")
        self.assertEqual([c["subject"] for c in main_only["data"]["commits"]], ["first commit"])
        branches = self.call("repo branches project")
        names = {b["name"]: b["current"] for b in branches["data"]["branches"]}
        self.assertEqual(names, {"feature": True, "main": False})
        show = self.call("repo show HEAD project")
        self.assertEqual(show["data"]["commit"]["subject"], "add b")
        self.assertEqual(show["data"]["body"], "Body line explaining b.")
        self.assertEqual([f["path"] for f in show["data"]["files"]], ["b.txt"])

    def test_diff_variants(self) -> None:
        worktree = self.call("repo diff project --patch")
        self.assertEqual([(f["path"], f["added"]) for f in worktree["data"]["files"]], [("a.txt", 1)])
        self.assertIn("+two", worktree["data"]["patch"])
        staged = self.call("repo diff project --staged")
        self.assertEqual([f["path"] for f in staged["data"]["files"]], ["staged.txt"])
        ranged = self.call("repo diff project --rev main..feature")
        self.assertEqual([f["path"] for f in ranged["data"]["files"]], ["b.txt"])

    def test_option_injection_is_rejected(self) -> None:
        for command in ("repo log project --rev --output=/tmp/x", "repo show --help project",
                        "repo show HEAD:a.txt project", "repo log project --rev 'a b'", "repo diff project --rev -p"):
            with self.subTest(command=command):
                result = self.call(command)
                self.assertFalse(result["success"])

    def test_program_executing_config_is_refused(self) -> None:
        cases = {
            "core.fsmonitor": f"touch {self.marker}; false",
            "filter.evil.clean": f"touch {self.marker}; cat",
            "diff.evil.textconv": f"touch {self.marker}; cat",
            "core.pager": f"touch {self.marker}; cat",
            "core.worktree": str(self.base),
        }
        for key, value in cases.items():
            with self.subTest(key=key):
                evil = self.evil_repo(key, value)
                (evil / ".gitattributes").write_text("* filter=evil diff=evil\n", encoding="utf-8")
                for action in ("status", "log", "diff", "branches"):
                    result = self.call(f"repo {action} evil")
                    self.assertFalse(result["success"])
                    self.assertIn("program-executing", result["output"])
                self.assertFalse(self.marker.exists())
                shutil.rmtree(evil)

    def test_forced_config_neutralises_fsmonitor_even_without_audit(self) -> None:
        evil = self.evil_repo("core.fsmonitor", f"touch {self.marker}; false")
        repo = locate_repository(self.ctx, "evil")
        asyncio.run(repo_skill._run_git(repo, ["status", "--porcelain=v2", "-z"]))
        self.assertFalse(self.marker.exists())
        self.assertTrue(evil.exists())

    def test_gitfile_and_linked_git_dirs_are_refused(self) -> None:
        worktree = self.root / "linked"
        worktree.mkdir()
        (worktree / ".git").write_text(f"gitdir: {self.project / '.git'}\n", encoding="utf-8")
        with self.assertRaisesRegex(GitError, "real directory"):
            locate_repository(self.ctx, "linked")
        if os.name != "nt":
            other = self.root / "other"
            other.mkdir()
            (other / ".git").symlink_to(self.project / ".git", target_is_directory=True)
            self.assertIn("real directory", self.call("repo status other")["output"])

    def test_missing_repo_and_escape(self) -> None:
        self.assertIn("No git repository", self.call("repo status")["output"])
        self.assertFalse(self.call("repo status ../")["success"])
        self.assertFalse(self.call("repo status /tmp")["success"])
        self.assertFalse(self.call("repo frobnicate")["success"])

    def test_parse_status_handles_renames_and_conflicts(self) -> None:
        raw = "\0".join([
            "# branch.oid abc", "# branch.head main", "# branch.upstream origin/main", "# branch.ab +2 -1",
            "2 R. N... 100644 100644 100644 aaa bbb R100 new name.txt", "old name.txt",
            "u UU N... 100644 100644 100644 100644 a b c conflict.txt", "? new.txt", "",
        ])
        info = parse_status(raw)
        self.assertEqual((info["ahead"], info["behind"]), (2, 1))
        self.assertEqual(info["staged"], [{"path": "new name.txt", "code": "R.", "from": "old name.txt"}])
        self.assertEqual(info["conflicts"][0]["path"], "conflict.txt")
        self.assertEqual(info["untracked"], ["new.txt"])


if __name__ == "__main__":
    unittest.main()
