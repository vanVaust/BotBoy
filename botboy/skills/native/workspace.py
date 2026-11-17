"""Workspace confinement for filesystem-reading native skills.

Every path a user supplies is interpreted relative to one operator-configured
workspace root. The rules are deliberately strict and identical for all
skills that read files:

* no absolute paths, drive letters, ``~`` or ``..`` segments;
* no hidden components (``.env``, ``.git``, ``.ssh``...): secrets commonly
  live there and no skill needs them through this interface;
* no symlinks or junctions anywhere on the path, checked per component with
  ``lstat`` so a link inside the workspace cannot point outside it;
* files are opened with ``O_NOFOLLOW`` where available and re-checked with
  ``fstat`` so a path swapped for a link between check and open is refused;
* every read is size-capped.
"""
from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

MAX_PATH_CHARS = 512
DEFAULT_MAX_FILE_BYTES = 2 * 1024 * 1024
_DRIVE_RE = re.compile(r"^[A-Za-z]:")


class WorkspaceError(ValueError):
    """Raised when a path violates workspace confinement."""


def require_root(root: Optional[Path]) -> Path:
    if root is None:
        raise WorkspaceError(
            "No workspace configured. Set skills.workspace_root in config.yaml or BOTBOY_WORKSPACE."
        )
    try:
        resolved = Path(root).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise WorkspaceError(f"Workspace root is not accessible: {root}") from exc
    if not resolved.is_dir():
        raise WorkspaceError(f"Workspace root is not a directory: {resolved}")
    return resolved


def _normalized_parts(user_path: str) -> List[str]:
    raw = str(user_path or "").strip()
    if not raw or raw in {".", "./"}:
        return []
    if "\x00" in raw or len(raw) > MAX_PATH_CHARS:
        raise WorkspaceError("Invalid or overly long path.")
    normalized = raw.replace("\\", "/")
    if normalized.startswith(("/", "~")) or _DRIVE_RE.match(normalized):
        raise WorkspaceError("Paths must be relative to the workspace root.")
    parts = [part for part in normalized.split("/") if part and part != "."]
    for part in parts:
        if part == "..":
            raise WorkspaceError("Parent-directory segments ('..') are not allowed.")
        if part.startswith("."):
            raise WorkspaceError(f"Hidden path components are not accessible: {part!r}")
    return parts


def _is_link_like(path: Path) -> bool:
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction and is_junction())


def resolve(root: Optional[Path], user_path: str, *, expect: str = "any") -> Path:
    """Resolve ``user_path`` inside the workspace or raise ``WorkspaceError``.

    ``expect`` is ``"file"``, ``"dir"`` or ``"any"``. The returned path is
    absolute, exists, and every component below the root is a real entry.
    """
    base = require_root(root)
    current = base
    for part in _normalized_parts(user_path):
        current = current / part
        if not os.path.lexists(current):
            raise WorkspaceError(f"Not found in workspace: {user_path}")
        if _is_link_like(current):
            raise WorkspaceError(f"Symbolic links are not followed: {user_path}")
    try:
        resolved = current.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise WorkspaceError(f"Not accessible: {user_path}") from exc
    if not resolved.is_relative_to(base):
        raise WorkspaceError("Path escapes the workspace root.")
    if expect == "file" and not resolved.is_file():
        raise WorkspaceError(f"Not a regular file: {user_path or '.'}")
    if expect == "dir" and not resolved.is_dir():
        raise WorkspaceError(f"Not a directory: {user_path or '.'}")
    return resolved


def relative(root: Optional[Path], path: Path) -> str:
    base = require_root(root)
    rel = path.relative_to(base).as_posix()
    return rel or "."


def read_bytes(path: Path, max_bytes: int = DEFAULT_MAX_FILE_BYTES) -> Tuple[bytes, bool]:
    """Read at most ``max_bytes`` from a regular file without following links.

    Returns ``(data, truncated)``.
    """
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise WorkspaceError(f"Cannot open {path.name}: {exc.strerror or exc}") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise WorkspaceError(f"Not a regular file: {path.name}")
        chunks: List[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = os.read(fd, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    finally:
        os.close(fd)
    data = b"".join(chunks)
    if len(data) > max_bytes:
        return data[:max_bytes], True
    return data, False


def looks_binary(data: bytes) -> bool:
    return b"\x00" in data[:8192]


def read_text(path: Path, max_bytes: int = DEFAULT_MAX_FILE_BYTES, *, allow_truncation: bool = True) -> Tuple[str, bool]:
    data, truncated = read_bytes(path, max_bytes)
    if truncated and not allow_truncation:
        raise WorkspaceError(f"File exceeds the {max_bytes}-byte limit: {path.name}")
    if looks_binary(data):
        raise WorkspaceError(f"Binary file refused: {path.name}")
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return data.decode("utf-8", errors="replace"), truncated


@dataclass(frozen=True)
class WalkEntry:
    path: Path
    relative: str
    is_dir: bool
    depth: int


def walk(root: Optional[Path], start: Path, *, max_depth: int, max_entries: int) -> Iterator[WalkEntry]:
    """Yield entries below ``start`` in pre-order, skipping hidden names and links."""
    base = require_root(root)
    emitted = 0
    # Each stack frame is an iterator over one directory's visible entries.
    stack: List[Tuple[Iterator[Tuple[Path, bool]], int]] = [(_visible_children(start), 0)]
    while stack:
        children, depth = stack[-1]
        child = next(children, None)
        if child is None:
            stack.pop()
            continue
        path, is_dir = child
        yield WalkEntry(path=path, relative=path.relative_to(base).as_posix(), is_dir=is_dir, depth=depth)
        emitted += 1
        if emitted >= max_entries:
            return
        if is_dir and depth + 1 < max_depth:
            stack.append((_visible_children(path), depth + 1))


def _visible_children(directory: Path) -> Iterator[Tuple[Path, bool]]:
    try:
        with os.scandir(directory) as iterator:
            entries = sorted(iterator, key=lambda entry: entry.name)
    except OSError:
        return
    for entry in entries:
        if entry.name.startswith("."):
            continue
        try:
            if entry.is_symlink() or _is_link_like(Path(entry.path)):
                continue
            is_dir = entry.is_dir(follow_symlinks=False)
            is_file = entry.is_file(follow_symlinks=False)
        except OSError:
            continue
        if is_dir or is_file:
            yield Path(entry.path), is_dir
