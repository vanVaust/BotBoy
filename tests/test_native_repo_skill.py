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

    def promisor_repo(self) -> Path:
        """Partial-clone repo whose committed blob is missing locally (lazy fetch)."""
        evil = self.root / "promisor"
        evil.mkdir()
        git(evil, "init", "-q")
        git(evil, "config", "user.email", "t@example.invalid")
        git(evil, "config", "user.name", "T")
        (evil / "r").write_text("payload\n", encoding="utf-8")
        git(evil, "add", "r")
        git(evil, "commit", "-q", "-m", "r")
        blob = subprocess.run(["git", "rev-parse", "HEAD:r"], cwd=evil, capture_output=True, text=True,
                              check=True).stdout.strip()
        obj = evil / ".git" / "objects" / blob[:2] / blob[2:]
        os.chmod(obj, 0o644)  # git writes objects read-only; Windows refuses to unlink those
        obj.unlink()
        git(evil, "config", "core.repositoryformatversion", "1")
        git(evil, "config", "extensions.partialclone", "origin")
        git(evil, "config", "remote.origin.url", str(self.project))
        git(evil, "config", "remote.origin.promisor", "true")
        git(evil, "config", "remote.origin.uploadpack", f"touch {self.marker}; false")
        return evil

    def test_promisor_lazy_fetch_cannot_run_upload_pack(self) -> None:
        # Review finding: a lazy fetch ran remote.origin.uploadpack.
        self.promisor_repo()
        result = self.call("repo show HEAD promisor")
        self.assertFalse(result["success"])
        self.assertIn("program-executing", result["output"])
        repo = locate_repository(self.ctx, "promisor")
        with self.assertRaises(GitError):
            asyncio.run(repo_skill._run_git(repo, ["show", "HEAD:r"]))
        self.assertFalse(self.marker.exists())

    def test_truncated_config_listing_is_refused(self) -> None:
        # Review finding: padding pushed filter.evil.clean past the output cap.
        evil = self.evil_repo("filter.evil.clean", f"touch {self.marker}; cat")
        config = evil / ".git" / "config"
        padding = "".join(f"[pad \"k{i}\"]\n\tv = 1\n" for i in range(400))
        config.write_text(padding + config.read_text(encoding="utf-8"), encoding="utf-8")
        original = repo_skill.MAX_GIT_OUTPUT
        repo_skill.MAX_GIT_OUTPUT = 2048
        self.addCleanup(setattr, repo_skill, "MAX_GIT_OUTPUT", original)
        result = self.call("repo status evil")
        self.assertFalse(result["success"])
        self.assertIn("refusing a partial result", result["output"])
        self.assertFalse(self.marker.exists())

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


@unittest.skipUnless(GIT, "git is not installed")
class RepoOutputCapTests(unittest.TestCase):
    def test_large_output_is_capped_while_reading(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        project = root / "big"
        project.mkdir()
        git(project, "init", "-q")
        git(project, "config", "user.email", "t@example.invalid")
        git(project, "config", "user.name", "T")
        (project / "big.txt").write_text("line\n" * 200_000, encoding="utf-8")
        git(project, "add", "big.txt")
        git(project, "commit", "-q", "-m", "big")
        ctx = NativeSkillContext(workspace_root=root)
        repo = locate_repository(ctx, "big")
        original = repo_skill.MAX_GIT_OUTPUT
        repo_skill.MAX_GIT_OUTPUT = 4096
        self.addCleanup(setattr, repo_skill, "MAX_GIT_OUTPUT", original)
        text = asyncio.run(repo_skill._run_git(repo, ["show", "HEAD"], allow_truncation=True))
        self.assertLessEqual(len(text.encode()), 4096)
        # Default: structured output is never returned as a silent prefix.
        with self.assertRaisesRegex(GitError, "refusing a partial result"):
            asyncio.run(repo_skill._run_git(repo, ["show", "HEAD"]))

    def test_status_over_the_cap_is_refused_not_partial(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        project = root / "many"
        project.mkdir()
        git(project, "init", "-q")
        for i in range(400):
            (project / f"untracked_{i:04d}_{'x' * 40}.txt").write_text("x", encoding="utf-8")
        original = repo_skill.MAX_GIT_OUTPUT
        repo_skill.MAX_GIT_OUTPUT = 4096
        self.addCleanup(setattr, repo_skill, "MAX_GIT_OUTPUT", original)
        result = asyncio.run(repo_skill.run("repo status many", NativeSkillContext(workspace_root=root)))
        self.assertFalse(result["success"])
        self.assertIn("refusing a partial result", result["output"])


@unittest.skipUnless(GIT, "git is not installed")
class RepoRedirectionTests(unittest.TestCase):
    """Review finding: .git/commondir made git read a repository outside the workspace."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name).resolve()
        self.outside = base / "private"
        self.outside.mkdir()
        git(self.outside, "init", "-q")
        git(self.outside, "config", "user.email", "t@example.invalid")
        git(self.outside, "config", "user.name", "T")
        (self.outside / "secret.txt").write_text("s\n", encoding="utf-8")
        git(self.outside, "add", "secret.txt")
        git(self.outside, "commit", "-q", "-m", "PRIVATE COMMIT MESSAGE")
        self.root = base / "workspace"
        self.ctx = NativeSkillContext(workspace_root=self.root)
        self.fake = self.root / "fake" / ".git"
        self.fake.mkdir(parents=True)

    def call(self, command: str) -> dict:
        return asyncio.run(repo_skill.run(command, self.ctx))

    def test_commondir_redirect_is_refused(self) -> None:
        (self.fake / "HEAD").write_text("ref: refs/heads/master\n", encoding="utf-8")
        (self.fake / "commondir").write_text(str(self.outside / ".git"), encoding="utf-8")
        result = self.call("repo show HEAD fake")
        self.assertFalse(result["success"])
        self.assertNotIn("PRIVATE", result["output"])
        self.assertIn("commondir", result["output"])

    def test_alternates_symlinks_and_includes_are_refused(self) -> None:
        shutil.rmtree(self.fake.parent)
        project = self.root / "fake"
        project.mkdir(parents=True)
        git(project, "init", "-q")
        alternates = project / ".git" / "objects" / "info" / "alternates"
        alternates.write_text(str(self.outside / ".git" / "objects"), encoding="utf-8")
        self.assertIn("alternates", self.call("repo log fake")["output"])
        alternates.unlink()
        git(project, "config", "include.path", str(self.outside / ".git" / "config"))
        self.assertIn("program-executing", self.call("repo log fake")["output"])
        git(project, "config", "--unset", "include.path")
        if os.name != "nt":
            (project / ".git" / "refs" / "heads" / "leak").symlink_to(self.outside / ".git" / "refs" / "heads" / "master")
            self.assertIn("symbolic link", self.call("repo log fake")["output"])


@unittest.skipUnless(GIT, "git is not installed")
class AnnotatedTagTests(unittest.TestCase):
    def test_show_peels_annotated_tags(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        project = root / "r"
        project.mkdir()
        git(project, "init", "-q")
        git(project, "config", "user.email", "t@example.invalid")
        git(project, "config", "user.name", "T")
        (project / "a.txt").write_text("a\n", encoding="utf-8")
        git(project, "add", "a.txt")
        git(project, "commit", "-q", "-m", "first commit", "-m", "commit body")
        git(project, "tag", "-a", "v1.0", "-m", "TAG MESSAGE")
        result = asyncio.run(repo_skill.run("repo show v1.0 r", NativeSkillContext(workspace_root=root)))
        self.assertTrue(result["success"], result["output"])
        self.assertRegex(result["data"]["commit"]["hash"], r"^[0-9a-f]{40}$")
        self.assertNotIn("TAG MESSAGE", result["output"])
        self.assertIn("commit body", result["output"])
