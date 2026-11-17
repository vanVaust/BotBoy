"""``repo`` — read-only inspection of git repositories inside the workspace.

Running git on an untrusted repository is a code-execution risk: a
repository's own ``.git/config`` can name programs git will run during
ordinary read commands (``core.fsmonitor`` during ``status``, clean/smudge
``filter`` drivers, ``diff.*.textconv``, external diff, pagers, hooks). This
skill therefore:

* runs git with a fixed argv (no shell) and a scrubbed environment —
  no system or global config, no terminal prompts, no optional index locks,
  explicit ``--git-dir``/``--work-tree`` so no repository discovery happens;
* forces safe values on the command line (fsmonitor off, hooks path empty,
  no pager, no external diff, no textconv, submodules ignored);
* reads the repository's effective config *before* running anything else and
  refuses repositories that configure any program-executing key (fail
  closed rather than trying to neutralise unknown driver names);
* requires ``.git`` to be a real directory inside the workspace (a ``.git``
  file or symlink could point the git dir anywhere);
* validates revision arguments and separates them with ``--end-of-options``
  so they can never be parsed as options;
* caps run time and output size.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Sequence

from botboy.skills.native import workspace
from botboy.skills.native.base import (
    NativeSkillContext,
    SkillUsageError,
    failure,
    parse_options,
    result,
    split_command,
    truncate,
)

GIT_TIMEOUT_SECONDS = 10.0
MAX_GIT_OUTPUT = 2 * 1024 * 1024
MAX_PATCH_CHARS = 20000
_REV_RE = re.compile(r"(?!-)[A-Za-z0-9._/~^@{}+\-]{1,200}")
_DANGEROUS_CONFIG = [
    re.compile(pattern) for pattern in (
        r"^core\.(fsmonitor|hookspath|sshcommand|pager|editor|askpass|gitproxy|alternaterefscommand|worktree)$",
        r"^filter\.",
        r"^diff\.external$",
        r"^diff\..+\.(textconv|command)$",
        r"^merge\..+\.driver$",
        r"^pager\.",
        r"^gpg\.(.+\.)?program$",
        r"^status\.submodulesummary$",
        # Partial-clone lazy fetches spawn upload-pack, whose path the
        # repository can set (remote.<name>.uploadpack).
        r"^remote\..+\.(uploadpack|receivepack|promisor|proxy|vcs)$",
        r"^extensions\.partialclone$",
        r"^(uploadpack|protocol|credential)\.",
    )
]
_FORCED_CONFIG = (
    "core.fsmonitor=false",
    f"core.hooksPath={os.devnull}",
    "core.pager=cat",
    "core.untrackedCache=false",
    "diff.external=",
    "color.ui=false",
    "log.showSignature=false",
    "submodule.recurse=false",
    "status.submoduleSummary=false",
    # No transport of any kind: blocks lazy fetches and thus upload-pack.
    "protocol.allow=never",
    "fetch.negotiationAlgorithm=noop",
)

_USAGE = (
    "Usage (repository paths are relative to the workspace root; default '.'):\n"
    "  repo status [path]\n"
    "  repo log [path] [-n N | --max N] [--rev REV]\n"
    "  repo diff [path] [--staged] [--rev REV|A..B] [--patch]\n"
    "  repo branches [path]\n"
    "  repo show <rev> [path]"
)


class GitError(ValueError):
    """Repository refused or git invocation failed."""


@dataclass(frozen=True)
class Repository:
    worktree: Path
    git_dir: Path
    relative: str


def _git_executable() -> str:
    executable = shutil.which("git")
    if not executable:
        raise GitError("git executable not found on PATH")
    return executable


def _clean_env(home: str) -> Dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": home,
        "XDG_CONFIG_HOME": home,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_PAGER": "cat",
        "PAGER": "cat",
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_PROTOCOL_FROM_USER": "0",
        "LC_ALL": "C",
        "LANG": "C",
    }
    if os.name == "nt":
        for key in ("SystemRoot", "TEMP", "TMP"):
            if key in os.environ:
                env[key] = os.environ[key]
    return env


def locate_repository(ctx: NativeSkillContext, user_path: str) -> Repository:
    worktree = workspace.resolve(ctx.workspace_root, user_path, expect="dir")
    git_dir = worktree / ".git"
    try:
        info = os.lstat(git_dir)
    except OSError as exc:
        raise GitError(f"No git repository at {user_path or '.'} (missing .git directory)") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise GitError("'.git' must be a real directory inside the workspace (gitfiles and links are refused)")
    return Repository(worktree=worktree, git_dir=git_dir, relative=workspace.relative(ctx.workspace_root, worktree))


async def _run_git(repo: Repository, args: Sequence[str], *, forced: bool = True,
                   allow_truncation: bool = True) -> str:
    argv: List[str] = [_git_executable(), "--no-pager", "--no-optional-locks",
                       f"--git-dir={repo.git_dir}", f"--work-tree={repo.worktree}"]
    if forced:
        for setting in _FORCED_CONFIG:
            argv += ["-c", setting]
    argv += list(args)
    with tempfile.TemporaryDirectory(prefix="botboy-git-home-") as home:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(repo.worktree),
            env=_clean_env(home),
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=GIT_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise GitError(f"git {args[0]} exceeded {GIT_TIMEOUT_SECONDS:g}s") from None
    if process.returncode != 0:
        message = stderr.decode("utf-8", "replace").strip().splitlines()
        raise GitError(f"git {args[0]} failed: {message[-1] if message else f'exit {process.returncode}'}")
    if len(stdout) > MAX_GIT_OUTPUT and not allow_truncation:
        raise GitError(f"git {args[0]} output exceeds {MAX_GIT_OUTPUT} bytes; refusing a partial result")
    return stdout[:MAX_GIT_OUTPUT].decode("utf-8", "replace")


async def audit_config(repo: Repository) -> List[str]:
    """Return config keys that would make git execute repository-chosen programs."""
    # A truncated listing could hide a dangerous key after harmless padding:
    # the audit must see every key or refuse the repository.
    listing = await _run_git(repo, ["config", "--list", "--includes", "--name-only"], forced=False,
                             allow_truncation=False)
    flagged = []
    for key in listing.splitlines():
        lowered = key.strip().lower()
        if lowered and any(pattern.search(lowered) for pattern in _DANGEROUS_CONFIG):
            flagged.append(key.strip())
    return sorted(set(flagged))


async def _checked_repo(ctx: NativeSkillContext, user_path: str) -> Repository:
    repo = locate_repository(ctx, user_path)
    flagged = await audit_config(repo)
    if flagged:
        raise GitError(
            "Repository config declares program-executing settings; refusing to run git: " + ", ".join(flagged)
        )
    return repo


def _validate_rev(rev: str) -> str:
    if not _REV_RE.fullmatch(rev):
        raise SkillUsageError(f"Invalid revision {rev!r}")
    return rev


def parse_status(raw: str) -> Dict[str, Any]:
    info: Dict[str, Any] = {"branch": None, "oid": None, "upstream": None, "ahead": 0, "behind": 0,
                            "staged": [], "unstaged": [], "untracked": [], "conflicts": []}
    entries = raw.split("\0")
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry:
            continue
        if entry.startswith("# branch.head "):
            info["branch"] = entry[len("# branch.head "):]
        elif entry.startswith("# branch.oid "):
            info["oid"] = entry[len("# branch.oid "):]
        elif entry.startswith("# branch.upstream "):
            info["upstream"] = entry[len("# branch.upstream "):]
        elif entry.startswith("# branch.ab "):
            match = re.match(r"# branch\.ab \+(\d+) -(\d+)", entry)
            if match:
                info["ahead"], info["behind"] = int(match.group(1)), int(match.group(2))
        elif entry[0] in "12":
            fields = entry.split(" ", 8 if entry[0] == "1" else 9)
            xy, path = fields[1], fields[-1]
            record = {"path": path, "code": xy}
            if entry[0] == "2" and index < len(entries):
                record["from"] = entries[index]
                index += 1
            if xy[0] != ".":
                info["staged"].append(record)
            if xy[1] != ".":
                info["unstaged"].append(record)
        elif entry[0] == "u":
            info["conflicts"].append({"path": entry.split(" ", 10)[-1], "code": entry.split(" ")[1]})
        elif entry[0] == "?":
            info["untracked"].append(entry[2:])
    return info


def _render_status(repo: Repository, info: Dict[str, Any]) -> str:
    branch = info["branch"] or "(unknown)"
    lines = [f"{repo.relative}: on {branch}" + (f" @ {info['oid'][:10]}" if info["oid"] and info["oid"] != "(initial)" else "")]
    if info["upstream"]:
        lines.append(f"upstream {info['upstream']}: ahead {info['ahead']}, behind {info['behind']}")
    sections = (("staged", "Staged"), ("unstaged", "Not staged"), ("conflicts", "Conflicts"))
    for key, label in sections:
        items = info[key]
        if items:
            lines.append(f"{label} ({len(items)}):")
            lines.extend(f"  {item['code']} {item['path']}" + (f" (from {item['from']})" if item.get("from") else "") for item in items[:100])
    if info["untracked"]:
        lines.append(f"Untracked ({len(info['untracked'])}):")
        lines.extend(f"  {path}" for path in info["untracked"][:100])
    if not any(info[key] for key in ("staged", "unstaged", "conflicts", "untracked")):
        lines.append("Working tree clean.")
    return "\n".join(lines)


def parse_log(raw: str) -> List[Dict[str, str]]:
    commits = []
    for record in raw.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        parts = record.split("\x1f")
        if len(parts) != 6:
            continue
        commits.append({"hash": parts[0], "short": parts[1], "author": parts[2], "email": parts[3],
                        "date": parts[4], "subject": parts[5]})
    return commits


def parse_numstat(raw: str) -> List[Dict[str, Any]]:
    files = []
    for line in raw.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        added, removed, path = parts
        files.append({"path": path, "added": None if added == "-" else int(added),
                      "removed": None if removed == "-" else int(removed), "binary": added == "-"})
    return files


async def _status(ctx: NativeSkillContext, args: List[str]) -> dict:
    opts = parse_options(args)
    if len(opts.positionals) > 1:
        raise SkillUsageError("repo status [path]")
    repo = await _checked_repo(ctx, opts.positionals[0] if opts.positionals else "")
    raw = await _run_git(repo, ["status", "--porcelain=v2", "--branch", "-z", "--untracked-files=normal",
                                "--ignore-submodules=all"])
    info = parse_status(raw)
    return result("repo", _render_status(repo, info), action="status", repo=repo.relative, **info)


async def _log(ctx: NativeSkillContext, args: List[str]) -> dict:
    normalized = ["--max" if token == "-n" else token for token in args]
    opts = parse_options(normalized, valued=("max", "rev"))
    if len(opts.positionals) > 1:
        raise SkillUsageError("repo log [path] [-n N] [--rev REV]")
    count = opts.get_int("max", 10, minimum=1, maximum=200)
    repo = await _checked_repo(ctx, opts.positionals[0] if opts.positionals else "")
    git_args = ["log", "--no-color", "--no-ext-diff", "--no-textconv", f"--max-count={count}",
                "--format=%H%x1f%h%x1f%an%x1f%ae%x1f%aI%x1f%s%x1e", "--end-of-options"]
    if opts.get("rev"):
        git_args.append(_validate_rev(opts.get("rev") or ""))
    git_args.append("--")
    commits = parse_log(await _run_git(repo, git_args))
    lines = [f"{repo.relative}: {len(commits)} commit(s)"]
    lines.extend(f"{c['short']} {c['date'][:10]} {c['author']}: {c['subject']}" for c in commits)
    return result("repo", "\n".join(lines), action="log", repo=repo.relative, commits=commits)


async def _diff(ctx: NativeSkillContext, args: List[str]) -> dict:
    opts = parse_options(args, valued=("rev",), flags=("staged", "cached", "patch"))
    if len(opts.positionals) > 1:
        raise SkillUsageError("repo diff [path] [--staged] [--rev REV] [--patch]")
    repo = await _checked_repo(ctx, opts.positionals[0] if opts.positionals else "")
    base = ["diff", "--no-color", "--no-ext-diff", "--no-textconv", "--ignore-submodules=all"]
    if opts.flag("staged") or opts.flag("cached"):
        base.append("--cached")
    tail = ["--end-of-options"]
    if opts.get("rev"):
        tail.append(_validate_rev(opts.get("rev") or ""))
    tail.append("--")
    files = parse_numstat(await _run_git(repo, base + ["--numstat"] + tail))
    added = sum(item["added"] or 0 for item in files)
    removed = sum(item["removed"] or 0 for item in files)
    scope = "staged" if "--cached" in base else (opts.get("rev") or "working tree")
    lines = [f"{repo.relative}: {len(files)} file(s) changed ({scope}), +{added} -{removed}"]
    lines.extend(
        f"  {'bin' if item['binary'] else '+' + str(item['added']) + ' -' + str(item['removed'])}\t{item['path']}"
        for item in files[:200]
    )
    patch = ""
    patch_truncated = False
    if opts.flag("patch") and files:
        patch, patch_truncated = truncate(await _run_git(repo, base + ["--patch"] + tail), MAX_PATCH_CHARS)
        lines.append("")
        lines.append(patch)
    return result("repo", "\n".join(lines), action="diff", repo=repo.relative, scope=scope, files=files,
                  added=added, removed=removed, patch=patch, patch_truncated=patch_truncated)


async def _branches(ctx: NativeSkillContext, args: List[str]) -> dict:
    opts = parse_options(args)
    if len(opts.positionals) > 1:
        raise SkillUsageError("repo branches [path]")
    repo = await _checked_repo(ctx, opts.positionals[0] if opts.positionals else "")
    raw = await _run_git(repo, [
        "for-each-ref", "--sort=-committerdate",
        "--format=%(HEAD)%1f%(refname:short)%1f%(objectname:short)%1f%(committerdate:iso-strict)%1f%(upstream:short)%1f%(subject)",
        "refs/heads",
    ])
    branches = []
    for line in raw.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 6:
            branches.append({"current": parts[0] == "*", "name": parts[1], "commit": parts[2],
                             "date": parts[3], "upstream": parts[4], "subject": parts[5]})
    lines = [f"{repo.relative}: {len(branches)} branch(es)"]
    lines.extend(
        f"{'*' if b['current'] else ' '} {b['name']} {b['commit']} {b['date'][:10]}"
        + (f" [{b['upstream']}]" if b["upstream"] else "") + f" {b['subject']}"
        for b in branches
    )
    return result("repo", "\n".join(lines), action="branches", repo=repo.relative, branches=branches)


async def _show(ctx: NativeSkillContext, args: List[str]) -> dict:
    opts = parse_options(args)
    if not 1 <= len(opts.positionals) <= 2:
        raise SkillUsageError("repo show <rev> [path]")
    rev = _validate_rev(opts.positionals[0])
    repo = await _checked_repo(ctx, opts.positionals[1] if len(opts.positionals) > 1 else "")
    header = await _run_git(repo, ["show", "--no-color", "--no-patch",
                                   "--format=%H%x1f%h%x1f%an%x1f%ae%x1f%aI%x1f%s%x1e", "--end-of-options", rev, "--"])
    commits = parse_log(header)
    if not commits:
        raise GitError(f"Revision {rev!r} is not a commit")
    body = await _run_git(repo, ["show", "--no-color", "--no-patch", "--format=%b", "--end-of-options", rev, "--"])
    files = parse_numstat(await _run_git(repo, ["show", "--no-color", "--no-ext-diff", "--no-textconv",
                                                "--format=", "--numstat", "--end-of-options", rev, "--"]))
    commit = commits[0]
    lines = [f"{commit['hash']}", f"Author: {commit['author']} <{commit['email']}>", f"Date:   {commit['date']}",
             "", f"    {commit['subject']}"]
    if body.strip():
        lines.extend(f"    {line}" for line in body.strip().splitlines()[:40])
    lines.append("")
    lines.extend(f"  +{f['added']} -{f['removed']}\t{f['path']}" if not f["binary"] else f"  bin\t{f['path']}" for f in files[:200])
    return result("repo", "\n".join(lines), action="show", repo=repo.relative, commit=commit,
                  body=body.strip(), files=files)


async def run(command: str, ctx: NativeSkillContext) -> dict:
    try:
        _, tokens = split_command(command)
        if not tokens or tokens[0].lower() in {"help", "--help"}:
            return result("repo", _USAGE, usage=True) if tokens else failure("repo", _USAGE)
        action, args = tokens[0].lower(), tokens[1:]
        handlers = {"status": _status, "log": _log, "diff": _diff, "branches": _branches, "show": _show}
        handler = handlers.get(action)
        if handler is None:
            return failure("repo", f"Unknown repo action {action!r}.\n{_USAGE}")
        return await handler(ctx, args)
    except (SkillUsageError, workspace.WorkspaceError, GitError) as exc:
        return failure("repo", f"repo: {exc}")
