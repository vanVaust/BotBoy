"""``archive`` — inspect zip and tar(.gz/.bz2/.xz) archives without extracting.

    archive list downloads/project.zip
    archive check downloads/backup.tar.gz
    archive read downloads/project.zip src/README.md

Nothing is ever written to disk. The archive is opened through a descriptor
pinned beneath the workspace root, and every byte that is decompressed is
charged to a budget (bytes and wall-clock), so neither listing a
``.tar.gz`` nor verifying a zip can be turned into a decompression bomb.

Threat model (the archive is attacker-controlled):

* Decompression bombs: decompression is done here, with ``max_length``-bounded
  ``zlib``/``bz2``/``lzma`` calls (``zipfile``'s own reader decompresses
  bzip2/LZMA members without an output bound on older Pythons), so each call
  produces at most 64 KiB; a total budget and a deadline stop the stream. The
  LZMA decoder's memory is limited (``memlimit`` for xz, a dictionary-size
  ceiling for zip LZMA members). Declared sizes are never trusted: ``check``
  compares them with what the stream really produces.
* Parser resource use: a zip central directory is read only if it is at most
  4 MiB (bounding the number of entries ``zipfile`` materialises); tar header
  reads (long names, pax records) are capped at 256 KiB per read; member
  references are dropped while iterating, so memory stays constant.
* Unsafe entries are reported, never acted on: absolute paths, drive letters,
  ``..`` traversal, symlinks and hardlinks (and whether their targets escape),
  device files and FIFOs, setuid/setgid bits, duplicate and case-colliding
  names, Windows-reserved names, encrypted members, overlapping zip entries
  (the "overlapping files" bomb) and local/central zip name mismatches.
* Output hygiene: control, format, bidi and surrogate characters in names are
  escaped, so a member name cannot forge lines in the report.
"""
from __future__ import annotations

import asyncio
import bz2
import lzma
import os
import posixpath
import re
import stat
import struct
import tarfile
import time
import unicodedata
import zipfile
import zlib
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

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

CHUNK = 64 * 1024
STREAM_BUDGET_BYTES = 512 * 1024 * 1024
DEADLINE_S = 15.0
MAX_CENTRAL_DIRECTORY_BYTES = 4 * 1024 * 1024
MAX_HEADER_READ_BYTES = 256 * 1024
MAX_MEMBER_HEADER_BYTES = 1024 * 1024
MAX_SCAN_ENTRIES = 100_000
MAX_LISTED_ENTRIES = 1_000
MAX_SHOWN_LINES = 200
MAX_MEMBER_BYTES = 2 * 1024 * 1024
MAX_LINK_TARGET_BYTES = 4096
LZMA_MEMLIMIT = 128 * 1024 * 1024
LZMA_MAX_DICT = 64 * 1024 * 1024
RATIO_FLAG = 100
RATIO_MIN_BYTES = 1024 * 1024
HUGE_MEMBER_BYTES = 1024 * 1024 * 1024
DEFAULT_OUTPUT_CHARS = 8000

_GZIP_MAGIC = b"\x1f\x8b"
_BZIP2_MAGIC = b"BZh"
_XZ_MAGIC = b"\xfd7zXZ\x00"
_ZIP_MAGICS = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_DRIVE = re.compile(r"^[A-Za-z]:")
_WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}
)
# Unsafe: extracting with a naive tool can write outside the target directory,
# overwrite unrelated files, or explode in size. Caution: worth a look.
UNSAFE_FLAGS = frozenset({"absolute", "traversal", "link-escapes", "link-traversal", "link-unresolved",
                          "device", "overlap", "name-mismatch",
                          "size-mismatch", "crc-mismatch", "corrupt"})
_USAGE = (
    "Usage (archive paths are relative to the workspace root; zip, tar, tar.gz/.tgz, tar.bz2, tar.xz,\n"
    "single .gz/.bz2/.xz files):\n"
    "  archive list <archive> [--max N]\n"
    "  archive check <archive>\n"
    "  archive read <archive> <member> [--max-chars N]"
)


class ArchiveError(ValueError):
    """Refused, malformed or over-budget archive."""


class BudgetExceeded(ArchiveError):
    pass


def safe_name(text: str) -> str:
    """Escape characters that could forge or hide report content."""
    out: List[str] = []
    for char in text:
        category = unicodedata.category(char)
        if category in {"Cc", "Cf", "Cs", "Zl", "Zp"}:
            code = ord(char)
            out.append(f"\\x{code:02x}" if code <= 0xFF else f"\\u{code:04x}")
        else:
            out.append(char)
    return "".join(out)


# ── budgets and streams ─────────────────────────────────────────────────────

@dataclass
class Budget:
    # Read at construction so the module-level limits stay tunable.
    max_bytes: int = field(default_factory=lambda: STREAM_BUDGET_BYTES)
    deadline_s: float = field(default_factory=lambda: DEADLINE_S)
    used: int = 0
    started: float = field(default_factory=time.perf_counter)

    def charge(self, count: int) -> None:
        self.used += count
        if self.used > self.max_bytes:
            raise BudgetExceeded(f"decompressed more than {self.max_bytes} bytes (budget); possible bomb")
        self.tick()

    def tick(self) -> None:
        if time.perf_counter() - self.started > self.deadline_s:
            raise BudgetExceeded(f"stopped after {self.deadline_s:g}s (time budget)")


class _Inflater:
    """One compressed stream format behind a common, output-bounded API."""

    def __init__(self, kind: str, obj: Any = None) -> None:
        self.kind = kind
        self._obj = obj if obj is not None else self._new()

    def _new(self) -> Any:
        if self.kind == "gzip":
            return zlib.decompressobj(16 + zlib.MAX_WBITS)
        if self.kind == "deflate":
            return zlib.decompressobj(-zlib.MAX_WBITS)
        if self.kind == "bzip2":
            return bz2.BZ2Decompressor()
        if self.kind == "xz":
            return lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=LZMA_MEMLIMIT)
        raise ArchiveError(f"unsupported compression {self.kind}")

    def restart(self) -> None:
        self._obj = self._new()

    @property
    def eof(self) -> bool:
        return bool(self._obj.eof)

    @property
    def unused_data(self) -> bytes:
        return self._obj.unused_data

    def wants_input(self) -> bool:
        if hasattr(self._obj, "unconsumed_tail"):  # zlib hands unconsumed input back
            return not self._obj.unconsumed_tail
        return bool(self._obj.needs_input)

    def decompress(self, data: bytes, limit: int) -> bytes:
        """At most ``limit`` bytes of output; ``data`` only when input is wanted."""
        try:
            if hasattr(self._obj, "unconsumed_tail"):
                return self._obj.decompress(data or self._obj.unconsumed_tail, limit)
            return self._obj.decompress(data, limit)
        except (zlib.error, OSError, EOFError, lzma.LZMAError, ValueError) as exc:
            raise ArchiveError(f"corrupt {self.kind} data: {exc}") from exc


def inflate_step(inflater: "_Inflater", source: Callable[[], bytes], limit: int) -> Optional[bytes]:
    """One bounded decompression step.

    Returns output (possibly empty while headers are consumed) or ``None``
    when the input is exhausted and the decompressor has nothing left; the
    caller decides whether that means "complete" or "truncated". A final
    empty-input call drains output the decompressor still holds internally.
    """
    if inflater.wants_input():
        data = source()
        if not data:
            if inflater.eof:
                return None
            return inflater.decompress(b"", limit) or None
        return inflater.decompress(data, limit)
    return inflater.decompress(b"", limit)


class DecompressingReader:
    """Bounded reader over a compressed byte source (multi-member aware).

    ``read(n)`` returns at most ``n`` bytes and charges them to ``budget``;
    concatenated gzip/bzip2/xz members are decoded in sequence; trailing
    NUL padding is ignored and other trailing bytes are recorded.
    """

    def __init__(self, source: Callable[[int], bytes], kind: str, budget: Budget,
                 *, multi_member: bool = True) -> None:
        self._source = source
        self._inflater = _Inflater(kind)
        self._budget = budget
        self._multi = multi_member
        self._input = b""
        self._done = False
        self.trailing_garbage = False
        self.produced = 0

    def _next_input(self) -> bytes:
        if self._input:
            data, self._input = self._input, b""
            return data
        return self._source(CHUNK)

    def read(self, size: int) -> bytes:
        if size <= 0 or self._done:
            return b""
        size = min(size, CHUNK)
        while True:
            self._budget.tick()
            if self._inflater.eof:
                if not self._start_next_member():
                    self._done = True
                    return b""
            out = inflate_step(self._inflater, self._next_input, size)
            if out is None:
                if self._inflater.eof:
                    continue
                raise ArchiveError(f"{self._inflater.kind} stream ends early (truncated)")
            if self._inflater.eof:
                self._input = self._inflater.unused_data + self._input
            if out:
                self.produced += len(out)
                self._budget.charge(len(out))
                return out

    def _start_next_member(self) -> bool:
        rest = self._input
        while len(rest) < 8:
            more = self._source(CHUNK)
            if not more:
                break
            rest += more
        if not self._multi or not rest:
            self.trailing_garbage = self.trailing_garbage or bool(rest.strip(b"\x00"))
            return False
        if not rest.strip(b"\x00"):
            # Zero padding (common after tar.gz written by some tools): drain it.
            while True:
                self._budget.tick()
                more = self._source(CHUNK)
                if not more:
                    return False
                if more.strip(b"\x00"):
                    self.trailing_garbage = True
                    return False
        magic = {"gzip": _GZIP_MAGIC, "bzip2": _BZIP2_MAGIC, "xz": _XZ_MAGIC}.get(self._inflater.kind, b"")
        if not magic or not rest.startswith(magic):
            self.trailing_garbage = True
            return False
        self._inflater.restart()
        self._input = rest
        return True


class TarStream:
    """File-like object for ``tarfile`` with budgets and forward-only seeking.

    Over a compressed source, seeking forward decompresses and discards (and
    is charged); seeking backwards is refused. Over a plain file, seeks are
    real. Any single ``read`` is capped, which bounds tarfile's long-name and
    pax-header reads.
    """

    def __init__(self, raw: Any, budget: Budget, kind: str) -> None:
        self._raw = raw
        self._budget = budget
        self.kind = kind
        self._position = 0
        self._reader = None if kind == "tar" else DecompressingReader(raw.read, kind, budget)
        # Bytes tarfile may still read while parsing one member's headers
        # (None outside header parsing); see _GuardedTarInfo.
        self.header_allowance: Optional[int] = None

    @property
    def trailing_garbage(self) -> bool:
        return bool(self._reader and self._reader.trailing_garbage)

    def tell(self) -> int:
        return self._position

    def seekable(self) -> bool:
        return True

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            raise ArchiveError("unbounded read refused")
        if size > MAX_HEADER_READ_BYTES:
            raise ArchiveError(f"tar header block of {size} bytes exceeds {MAX_HEADER_READ_BYTES} bytes")
        if self.header_allowance is not None:
            self.header_allowance -= size
            if self.header_allowance < 0:
                raise ArchiveError(f"tar headers of one member exceed {MAX_MEMBER_HEADER_BYTES} bytes "
                                   "(long-name, pax or sparse-map chain)")
        chunks: List[bytes] = []
        remaining = size
        while remaining > 0:
            data = self._read_some(remaining)
            if not data:
                break
            chunks.append(data)
            remaining -= len(data)
        data = b"".join(chunks)
        self._position += len(data)
        return data

    def _read_some(self, size: int) -> bytes:
        if self._reader is not None:
            return self._reader.read(size)
        self._budget.tick()
        return self._raw.read(min(size, CHUNK))

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 1:
            offset += self._position
        elif whence != 0:
            raise ArchiveError("seek relative to the end is not supported")
        if offset < self._position and self._reader is not None:
            raise ArchiveError("backward seek in a compressed tar stream refused")
        if self._reader is None:
            self._budget.tick()
            self._raw.seek(offset)
            self._position = offset
            return offset
        while self._position < offset:
            data = self._reader.read(min(CHUNK, offset - self._position))
            if not data:
                break
            self._position += len(data)
        return self._position

    def close(self) -> None:  # the owner closes the raw file
        pass


class GuardedFile:
    """Raw-file wrapper for ``zipfile``: caps any single read (central directory)."""

    def __init__(self, raw: Any, size: int, budget: Budget) -> None:
        self._raw = raw
        self._size = size
        self._budget = budget

    def read(self, size: int = -1) -> bytes:
        position = self._raw.tell()
        if size is None or size < 0:
            size = max(0, self._size - position)
        if size > MAX_CENTRAL_DIRECTORY_BYTES + 65_557:
            raise ArchiveError(f"zip central directory larger than {MAX_CENTRAL_DIRECTORY_BYTES} bytes refused")
        self._budget.tick()
        return self._raw.read(size)

    def seek(self, offset: int, whence: int = 0) -> int:
        return self._raw.seek(offset, whence)

    def tell(self) -> int:
        return self._raw.tell()

    def seekable(self) -> bool:
        return True


# ── entries and safety analysis ─────────────────────────────────────────────

@dataclass
class Entry:
    name: str
    kind: str  # file | dir | symlink | hardlink | device | fifo | other
    size: int
    compressed: Optional[int] = None
    mode: Optional[int] = None
    link: str = ""
    flags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {"name": safe_name(self.name), "type": self.kind, "size": self.size,
                                "flags": list(self.flags)}
        if self.compressed is not None:
            data["compressed"] = self.compressed
        if self.mode is not None:
            data["mode"] = oct(self.mode & 0o7777)
        if self.link:
            data["link"] = safe_name(self.link)
        return data


def _path_flags(name: str) -> List[str]:
    flags: List[str] = []
    if name.startswith(("/", "\\")) or _DRIVE.match(name):
        flags.append("absolute")
    parts = [part for part in re.split(r"[\\/]", name) if part]
    if ".." in parts:
        flags.append("traversal")
    if "\\" in name:
        flags.append("backslash")
    if any(unicodedata.category(ch) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for ch in name):
        flags.append("control-chars")
    for part in parts:
        if part in {".", ".."}:
            continue
        stem = part.split(".", 1)[0].lower()
        if stem in _WINDOWS_RESERVED or part != part.rstrip(". ") or any(ch in part for ch in '<>:"|?*'):
            flags.append("windows-unsafe")
            break
    return flags


def _link_escapes(name: str, target: str, hard: bool) -> bool:
    if not target:
        return False
    if target.startswith(("/", "\\")) or _DRIVE.match(target):
        return True
    target = target.replace("\\", "/")
    base = "" if hard else posixpath.dirname(name.replace("\\", "/"))
    joined = posixpath.normpath(posixpath.join(base, target))
    return joined == ".." or joined.startswith("../") or joined.startswith("/")


MAX_LINK_HOPS = 40
LINK_RESOLUTION_STEPS = 2_000_000


class _Unresolvable(Exception):
    pass


def _parts(path: str) -> List[str]:
    # Extractors on Windows treat "\\" as a separator too.
    return [part for part in re.split(r"[\\/]", path) if part not in {"", "."}]


def _is_absolute(path: str) -> bool:
    return path.startswith(("/", "\\")) or bool(_DRIVE.match(path))


class LinkResolver:
    """Simulates how extraction follows symlinks created by earlier members.

    Each entry is checked lexically on its own, but links interact:
    ``a -> .`` then ``a/b -> ..`` then ``b/payload`` writes outside the
    destination although no single name or target looks unsafe. Paths are
    resolved component by component through the links recorded so far (in
    archive order, at their resolved locations), so such chains are caught.
    Hops per path and total work are bounded; exceeding them is reported.
    """

    def __init__(self) -> None:
        self.links: Dict[str, str] = {}
        self.steps = LINK_RESOLUTION_STEPS

    def resolve(self, parts: List[str], *, follow_last: bool) -> Optional[List[str]]:
        """Resolved components below the root, or ``None`` if the path escapes."""
        stack = list(reversed(parts))
        current: List[str] = []
        hops = 0
        while stack:
            self.steps -= 1
            if self.steps < 0:
                raise _Unresolvable("link resolution budget exhausted")
            part = stack.pop()
            if part in {"", "."}:
                continue
            if part == "..":
                if not current:
                    return None
                current.pop()
                continue
            key = "/".join(current + [part])
            target = self.links.get(key)
            if target is not None and (follow_last or stack):
                hops += 1
                if hops > MAX_LINK_HOPS:
                    raise _Unresolvable(f"more than {MAX_LINK_HOPS} link hops (loop?)")
                if _is_absolute(target):
                    return None
                # Relative to the directory holding the link (``current``).
                stack.extend(reversed(_parts(target) or ["."]))
                continue
            current.append(part)
        return current

    def check(self, entry: "Entry") -> List[str]:
        """Flags for ``entry``; records it when it is a symlink."""
        parts = _parts(entry.name)
        flags: List[str] = []
        if not parts or _is_absolute(entry.name):
            return flags  # lexical flags already cover these
        location = self.resolve(parts, follow_last=False)
        if location is None:
            return ["link-traversal"]
        if self.links and entry.kind in {"file", "hardlink"}:
            # Writing a file through a final component that is a symlink
            # writes to the link's target.
            if self.resolve(parts, follow_last=True) is None:
                flags.append("link-traversal")
        if entry.kind == "symlink":
            if self.resolve(location[:-1] + (_parts(entry.link) or ["."]), follow_last=True) is None \
                    or _is_absolute(entry.link):
                flags.append("link-escapes")
            self.links["/".join(location)] = entry.link
        elif entry.kind == "hardlink" and entry.link:
            if _is_absolute(entry.link) or self.resolve(_parts(entry.link), follow_last=True) is None:
                flags.append("link-escapes")
        return flags


def analyse(entries: List[Entry]) -> List[str]:
    """Flag entries in place; returns archive-level reasons."""
    seen: Dict[str, int] = {}
    folded: Dict[str, str] = {}
    resolver: Optional[LinkResolver] = LinkResolver()
    extra: List[str] = []
    for entry in entries:
        entry.flags[:0] = [flag for flag in _path_flags(entry.name) if flag not in entry.flags]
        if entry.kind in {"symlink", "hardlink"} and _link_escapes(entry.name, entry.link, entry.kind == "hardlink"):
            entry.flags.append("link-escapes")
        if resolver is not None:
            try:
                for flag in resolver.check(entry):
                    lexical = flag == "link-traversal" and "traversal" in entry.flags
                    if flag not in entry.flags and not lexical:
                        entry.flags.append(flag)
            except _Unresolvable as exc:
                extra.append(f"UNSAFE links could not be resolved: {exc}")
                entry.flags.append("link-unresolved")
                resolver = None
        if entry.kind in {"device", "fifo"}:
            entry.flags.append("device")
        if entry.mode is not None and entry.mode & (stat.S_ISUID | stat.S_ISGID):
            entry.flags.append("setuid")
        if entry.kind == "file" and entry.size >= HUGE_MEMBER_BYTES:
            entry.flags.append("huge")
        if (entry.compressed is not None and entry.size >= RATIO_MIN_BYTES
                and entry.size > RATIO_FLAG * max(entry.compressed, 1)):
            entry.flags.append("high-ratio")
        key = entry.name.rstrip("/")
        seen[key] = seen.get(key, 0) + 1
        if seen[key] == 2:
            entry.flags.append("duplicate")
        fold = key.casefold()
        if fold in folded and folded[fold] != key:
            entry.flags.append("case-collision")
        folded.setdefault(fold, key)
    return extra


def verdict(entries: List[Entry], extra: List[str]) -> Tuple[str, List[str]]:
    counts: Dict[str, int] = {}
    for entry in entries:
        for flag in entry.flags:
            counts[flag] = counts.get(flag, 0) + 1
    unsafe_extra = [reason[len("UNSAFE "):] for reason in extra if reason.startswith("UNSAFE ")]
    caution_extra = [reason[len("CAUTION "):] for reason in extra if reason.startswith("CAUTION ")]
    reasons = [f"{flag} ({count})" for flag, count in sorted(counts.items())] + unsafe_extra + caution_extra
    if any(flag in UNSAFE_FLAGS for flag in counts) or unsafe_extra:
        return "unsafe", reasons
    if reasons:
        return "caution", reasons
    return "ok", reasons


# ── format detection and opening ────────────────────────────────────────────

def _tar_checksum_ok(block: bytes) -> bool:
    if len(block) < 512:
        return False
    try:
        stored = int(block[148:156].replace(b"\x00", b" ").strip() or b"-1", 8)
    except ValueError:
        return False
    unsigned = sum(block[:148]) + 8 * 32 + sum(block[156:512])
    return stored == unsigned


def detect(head: bytes) -> str:
    if head.startswith(_GZIP_MAGIC):
        return "gzip"
    if head.startswith(_BZIP2_MAGIC):
        return "bzip2"
    if head.startswith(_XZ_MAGIC):
        return "xz"
    if head.startswith(_ZIP_MAGICS):
        return "zip"
    if head[257:262] == b"ustar" or _tar_checksum_ok(head[:512]):
        return "tar"
    return "unknown"


class OpenArchive:
    """A pinned, read-only handle on one workspace archive."""

    def __init__(self, ctx: NativeSkillContext, user_path: str) -> None:
        path = workspace.resolve(ctx.workspace_root, user_path, expect="file")
        self.rel = workspace.relative(ctx.workspace_root, path)
        self.name = path.name
        fd = workspace.open_regular(path, root=ctx.workspace_root)
        self.raw = os.fdopen(fd, "rb", buffering=0)
        try:
            self.size = os.fstat(fd).st_size
            head = self.raw.read(1024)
            self.raw.seek(0)
        except BaseException:
            self.raw.close()
            raise
        self.format = detect(head)

    def close(self) -> None:
        self.raw.close()

    def __enter__(self) -> "OpenArchive":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()


# ── zip ─────────────────────────────────────────────────────────────────────

def _zip_open(archive: OpenArchive, budget: Budget) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(GuardedFile(archive.raw, archive.size, budget))  # type: ignore[arg-type]
    except (zipfile.BadZipFile, zipfile.LargeZipFile, struct.error, UnicodeDecodeError, EOFError,
            OSError, ValueError, NotImplementedError) as exc:
        if isinstance(exc, ArchiveError):
            raise
        raise ArchiveError(f"not a readable zip archive: {exc}") from exc


def _zip_kind(info: zipfile.ZipInfo) -> Tuple[str, Optional[int]]:
    mode = (info.external_attr >> 16) & 0xFFFF if info.create_system == 3 else None
    if mode:
        if stat.S_ISLNK(mode):
            return "symlink", mode
        if stat.S_ISCHR(mode) or stat.S_ISBLK(mode) or stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode):
            return "device", mode
        if stat.S_ISDIR(mode):
            return "dir", mode
    if info.orig_filename.endswith(("/", "\\")):
        return "dir", mode
    return "file", mode


def _zip_data_start(archive: OpenArchive, info: zipfile.ZipInfo) -> Tuple[int, bytes]:
    archive.raw.seek(info.header_offset)
    header = archive.raw.read(30)
    if len(header) < 30 or header[:4] != b"PK\x03\x04":
        raise ArchiveError(f"bad local header for {safe_name(info.orig_filename)}")
    name_len, extra_len = struct.unpack("<HH", header[26:30])
    local_name = archive.raw.read(name_len)
    return info.header_offset + 30 + name_len + extra_len, local_name


def _zip_inflater(info: zipfile.ZipInfo, first: bytes) -> Tuple[Optional[Any], int]:
    """Return (decompressor or None for stored, bytes of method header to skip)."""
    method = info.compress_type
    if method == zipfile.ZIP_STORED:
        return None, 0
    if method == zipfile.ZIP_DEFLATED:
        return _Inflater("deflate"), 0
    if method == zipfile.ZIP_BZIP2:
        return _Inflater("bzip2"), 0
    if method == zipfile.ZIP_LZMA:
        if len(first) < 4:
            raise ArchiveError("truncated LZMA header")
        props_size = struct.unpack("<H", first[2:4])[0]
        props = first[4:4 + props_size]
        if props_size != 5 or len(props) != 5:
            raise ArchiveError("unsupported LZMA properties")
        lclppb = props[0]
        if lclppb >= 9 * 5 * 5:
            raise ArchiveError("invalid LZMA properties")
        dict_size = struct.unpack("<I", props[1:5])[0]
        if dict_size > LZMA_MAX_DICT:
            raise ArchiveError(f"LZMA dictionary of {dict_size} bytes exceeds {LZMA_MAX_DICT} bytes")
        filters = [{"id": lzma.FILTER_LZMA1, "dict_size": max(dict_size, 4096),
                    "lc": lclppb % 9, "lp": (lclppb // 9) % 5, "pb": lclppb // 45}]
        decoder = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=filters)
        return _Inflater("lzma", decoder), 4 + props_size
    raise ArchiveError(f"compression method {method} is not supported")


def zip_member_chunks(archive: OpenArchive, info: zipfile.ZipInfo, budget: Budget,
                      limit: Optional[int] = None) -> Iterator[bytes]:
    """Yield the member's decompressed bytes; verifies size and CRC at the end.

    Stops quietly after ``limit`` bytes (no verification then).
    """
    if info.flag_bits & 0x1:
        raise ArchiveError("member is encrypted")
    start, _ = _zip_data_start(archive, info)
    if start + info.compress_size > archive.size:
        raise ArchiveError("member data runs past the end of the archive")
    archive.raw.seek(start)
    left = info.compress_size
    first = archive.raw.read(min(left, 64)) if info.compress_type == zipfile.ZIP_LZMA else b""
    inflater, skip = _zip_inflater(info, first)
    pending = first[skip:]
    left -= len(first)
    produced = 0
    crc = 0

    def source() -> bytes:
        nonlocal left, pending
        if pending:
            data, pending = pending, b""
            return data
        if left <= 0:
            return b""
        data = archive.raw.read(min(CHUNK, left))
        left -= len(data)
        return data

    while True:
        budget.tick()
        if inflater is None:
            out = source()
            if not out:
                break
        else:
            if inflater.eof:
                break
            step = inflate_step(inflater, source, CHUNK)
            if step is None:
                if inflater.kind == "lzma" and produced == info.file_size:
                    break  # LZMA without end marker: complete when the size is reached
                raise ArchiveError("compressed data ends early")
            out = step
        if not out:
            continue
        if limit is not None and produced + len(out) > limit:
            yield out[: limit - produced]
            return
        produced += len(out)
        budget.charge(len(out))
        if produced > info.file_size:
            raise ArchiveError(f"member inflates beyond its declared size of {info.file_size} bytes")
        crc = zlib.crc32(out, crc)
        yield out
    if produced != info.file_size:
        raise ArchiveError(f"member produced {produced} bytes, declared {info.file_size}")
    if crc != info.CRC:
        raise ArchiveError("CRC mismatch")


def _zip_entries(archive: OpenArchive, zf: zipfile.ZipFile, budget: Budget,
                 read_links: bool = True) -> Tuple[List[Entry], List[str]]:
    infos = zf.infolist()
    extra: List[str] = []
    if len(infos) > MAX_SCAN_ENTRIES:
        extra.append(f"UNSAFE too many entries ({len(infos)} > {MAX_SCAN_ENTRIES})")
        infos = infos[:MAX_SCAN_ENTRIES]
    entries: List[Entry] = []
    spans: List[Tuple[int, int, int]] = []
    for index, info in enumerate(infos):
        budget.tick()
        kind, mode = _zip_kind(info)
        # orig_filename: zipfile rewrites "\\" to "/" in .filename on Windows,
        # which would make the analysis platform-dependent.
        entry = Entry(info.orig_filename, kind, info.file_size, info.compress_size, mode)
        if info.flag_bits & 0x1:
            entry.flags.append("encrypted")
        try:
            start, local_name = _zip_data_start(archive, info)
            expected = info.orig_filename.encode("utf-8" if info.flag_bits & 0x800 else "cp437",
                                                 errors="surrogateescape")
            if local_name != expected:
                entry.flags.append("name-mismatch")
            spans.append((info.header_offset, start + info.compress_size, index))
        except (ArchiveError, UnicodeEncodeError) as exc:
            entry.flags.append("corrupt")
            extra.append(f"{safe_name(info.orig_filename)}: {exc}")
        if kind == "symlink" and read_links and not info.flag_bits & 0x1:
            try:
                target = b"".join(zip_member_chunks(archive, info, budget, MAX_LINK_TARGET_BYTES))
                entry.link = target.decode("utf-8", "replace")
            except ArchiveError as exc:
                entry.flags.append("corrupt")
                extra.append(f"{safe_name(info.orig_filename)}: {exc}")
        entries.append(entry)
    spans.sort()
    for (start_a, end_a, index_a), (start_b, _end_b, index_b) in zip(spans, spans[1:]):
        if start_b < end_a:
            for index in (index_a, index_b):
                if "overlap" not in entries[index].flags:
                    entries[index].flags.append("overlap")
    return entries, extra


# ── tar ─────────────────────────────────────────────────────────────────────

def _tar_kind(info: tarfile.TarInfo) -> str:
    if info.isdir():
        return "dir"
    if info.issym():
        return "symlink"
    if info.islnk():
        return "hardlink"
    if info.ischr() or info.isblk():
        return "device"
    if info.isfifo():
        return "fifo"
    if info.isreg():
        return "file"
    return "other"


class _GuardedTarInfo(tarfile.TarInfo):
    """Bounds the header bytes ``tarfile`` reads for one member.

    A single read is already capped by ``TarStream``; this also caps chains of
    small reads (pax header -> GNU long name -> old-GNU sparse extension
    blocks), which tarfile would otherwise accumulate into an unbounded
    sparse map. ``fromtarfile`` is tarfile's public per-member entry point and
    re-enters itself for chained headers, so only the outermost call sets
    the allowance.
    """

    @classmethod
    def fromtarfile(cls, tarfile_obj: tarfile.TarFile) -> tarfile.TarInfo:  # type: ignore[override]
        stream = tarfile_obj.fileobj
        outermost = isinstance(stream, TarStream) and stream.header_allowance is None
        if outermost:
            stream.header_allowance = MAX_MEMBER_HEADER_BYTES
        try:
            return super().fromtarfile(tarfile_obj)
        finally:
            if outermost:
                stream.header_allowance = None


def _open_tar(archive: OpenArchive, budget: Budget) -> Tuple[tarfile.TarFile, TarStream]:
    kind = archive.format if archive.format in {"gzip", "bzip2", "xz"} else "tar"
    stream = TarStream(archive.raw, budget, kind)
    try:
        tar = tarfile.TarFile(fileobj=stream, mode="r", tarinfo=_GuardedTarInfo)  # type: ignore[arg-type]
    except ArchiveError:
        raise
    except tarfile.ReadError as exc:
        raise _NotTar(str(exc)) from exc
    except (tarfile.TarError, EOFError, ValueError, UnicodeError) as exc:
        raise ArchiveError(f"not a readable tar archive: {exc}") from exc
    return tar, stream


class _NotTar(ArchiveError):
    """The (decompressed) stream is not a tar archive."""


class _StreamDamaged(ArchiveError):
    """The compressed stream around a complete tar is truncated or corrupt."""


def _drain_stream(stream: "TarStream") -> None:
    """Decompress the rest of a compressed tar to its end (budgeted).

    tarfile stops at the end-of-archive blocks; the compressed footer (gzip
    CRC32/ISIZE, bzip2 stream CRC, xz index and check) is only validated
    when the decoder actually reaches its end of stream.
    """
    if stream.kind == "tar":
        return
    try:
        while stream.read(CHUNK):
            pass
    except BudgetExceeded:
        raise
    except ArchiveError as exc:
        raise _StreamDamaged(f"compressed stream is damaged after the last member: {exc}") from exc


def iter_tar(archive: OpenArchive, budget: Budget) -> Iterator[Tuple[tarfile.TarFile, tarfile.TarInfo]]:
    tar, stream = _open_tar(archive, budget)
    count = 0
    while True:
        try:
            info = tar.next()
        except (tarfile.TarError, EOFError, UnicodeError) as exc:
            raise ArchiveError(f"tar parse error after {count} entries: {exc}") from exc
        if info is None:
            _drain_stream(stream)
            return
        tar.members.clear()  # constant memory: nothing needs the member list
        count += 1
        if count > MAX_SCAN_ENTRIES:
            raise BudgetExceeded(f"more than {MAX_SCAN_ENTRIES} entries")
        yield tar, info


def _tar_entry(info: tarfile.TarInfo) -> Entry:
    kind = _tar_kind(info)
    return Entry(info.name, kind, info.size if kind == "file" else 0, None, info.mode,
                 info.linkname if kind in {"symlink", "hardlink"} else "")


def _single_stream_name(archive: OpenArchive) -> str:
    lowered = archive.name.lower()
    for suffix in (".gz", ".tgz", ".bz2", ".tbz2", ".xz", ".txz"):
        if lowered.endswith(suffix):
            stem = archive.name[: -len(suffix)]
            return stem + ".tar" if suffix.startswith(".t") else stem
    return archive.name + ".out"


def _single_stream(archive: OpenArchive, budget: Budget, limit: Optional[int] = None) -> Iterator[bytes]:
    archive.raw.seek(0)
    reader = DecompressingReader(archive.raw.read, archive.format, budget)
    produced = 0
    while True:
        data = reader.read(CHUNK)
        if not data:
            return
        if limit is not None and produced + len(data) > limit:
            yield data[: limit - produced]
            return
        produced += len(data)
        yield data


# ── actions ─────────────────────────────────────────────────────────────────

@dataclass
class Listing:
    format: str
    entries: List[Entry]
    extra: List[str]
    stopped: str = ""
    decompressed: Optional[int] = None


def collect(archive: OpenArchive, budget: Budget) -> Listing:
    if archive.format == "zip" or archive.format == "unknown":
        try:
            zf = _zip_open(archive, budget)
        except ArchiveError:
            if archive.format == "zip":
                raise
            raise ArchiveError("unsupported archive format (expected zip, tar, gzip, bzip2 or xz)") from None
        entries, extra = _zip_entries(archive, zf, budget)
        extra.extend(analyse(entries))
        return Listing("zip", entries, extra)
    label = {"tar": "tar", "gzip": "tar.gz", "bzip2": "tar.bz2", "xz": "tar.xz"}[archive.format]
    entries: List[Entry] = []
    extra: List[str] = []
    stopped = ""
    try:
        for _tar, info in iter_tar(archive, budget):
            entries.append(_tar_entry(info))
    except _NotTar:
        if archive.format == "tar":
            raise ArchiveError("not a readable tar archive") from None
        total = 0
        budget = _fresh(budget)
        try:
            for chunk in _single_stream(archive, budget):
                total += len(chunk)
        except BudgetExceeded as exc:
            stopped = str(exc)
        entry = Entry(_single_stream_name(archive), "file", total, archive.size)
        analyse([entry])
        if stopped:
            extra.append(f"UNSAFE {stopped}")
        return Listing(archive.format, [entry], extra, stopped, total)
    except BudgetExceeded as exc:
        stopped = str(exc)
        extra.append(f"UNSAFE {stopped}")
    except _StreamDamaged as exc:
        # Every header was read, but the compressed stream ends badly
        # (truncated footer, checksum mismatch): nothing is verified.
        stopped = str(exc)
        extra.append(f"UNSAFE {stopped}")
    extra.extend(analyse(entries))
    decompressed = budget.used if archive.format != "tar" else None
    return Listing(label, entries, extra, stopped, decompressed)


def _fresh(budget: Budget) -> Budget:
    """Remaining time, full byte budget (for a second pass over the input)."""
    remaining = budget.deadline_s - (time.perf_counter() - budget.started)
    return Budget(max_bytes=budget.max_bytes, deadline_s=max(0.1, remaining))


def _summary(archive: OpenArchive, listing: Listing) -> Tuple[str, Dict[str, Any]]:
    files = [e for e in listing.entries if e.kind == "file"]
    declared = sum(e.size for e in files)
    ratio = None
    expanded = listing.decompressed if listing.decompressed is not None else declared
    if archive.size:
        ratio = round(expanded / archive.size, 2)
    extra = list(listing.extra)
    if ratio is not None and ratio > RATIO_FLAG and expanded >= RATIO_MIN_BYTES:
        extra.append(f"CAUTION overall compression ratio {ratio}")
    level, reasons = verdict(listing.entries, extra)
    info = {
        "format": listing.format, "entries": len(listing.entries), "files": len(files),
        "dirs": sum(1 for e in listing.entries if e.kind == "dir"),
        "links": sum(1 for e in listing.entries if e.kind in {"symlink", "hardlink"}),
        "declared_bytes": declared, "archive_bytes": archive.size, "ratio": ratio,
        "verdict": level, "reasons": reasons, "incomplete": bool(listing.stopped),
    }
    if listing.decompressed is not None:
        info["decompressed_bytes"] = listing.decompressed
    line = (f"{archive.rel}: {listing.format}, {info['entries']} entr{'y' if info['entries'] == 1 else 'ies'} "
            f"({info['files']} files, {info['dirs']} dirs, {info['links']} links), {declared} bytes "
            f"uncompressed / {archive.size} bytes on disk (ratio {ratio})")
    if listing.stopped:
        line += f"\nINCOMPLETE: {listing.stopped}"
    line += f"\nverdict: {level}" + (f" - {', '.join(reasons)}" if reasons else "")
    return line, info


def _entry_line(entry: Entry) -> str:
    size = f"{entry.size:>12}"
    kind = {"file": " ", "dir": "d", "symlink": "l", "hardlink": "h", "device": "c", "fifo": "p"}.get(entry.kind, "?")
    name = safe_name(entry.name)
    if entry.link:
        name += f" -> {safe_name(entry.link)}"
    flags = f"  [{', '.join(entry.flags)}]" if entry.flags else ""
    return f"{kind} {size}  {name}{flags}"


def do_list(ctx: NativeSkillContext, user_path: str, max_lines: int) -> dict:
    budget = Budget()
    with OpenArchive(ctx, user_path) as archive:
        listing = collect(archive, budget)
        header, info = _summary(archive, listing)
    flagged = [e for e in listing.entries if e.flags]
    shown = (flagged + [e for e in listing.entries if not e.flags])[:max_lines] if flagged else listing.entries[:max_lines]
    lines = [header, *(_entry_line(e) for e in shown)]
    if len(listing.entries) > len(shown):
        lines.append(f"... {len(listing.entries) - len(shown)} more entries (flagged entries are listed first)")
    lines.extend(f"note: {note}" for note in listing.extra if not note.startswith(("UNSAFE ", "CAUTION ")))
    output, _ = truncate("\n".join(lines), 12_000)
    return result("archive", output, action="list", path=archive.rel, **info,
                  listed=[e.to_dict() for e in listing.entries[:MAX_LISTED_ENTRIES]],
                  listed_truncated=len(listing.entries) > MAX_LISTED_ENTRIES)


def do_check(ctx: NativeSkillContext, user_path: str) -> dict:
    budget = Budget()
    problems: List[str] = []
    verified = 0
    work = budget
    with OpenArchive(ctx, user_path) as archive:
        listing = collect(archive, budget)
        stopped = listing.stopped
        if listing.format == "zip" and not stopped:
            # Declared sizes and CRCs are claims; decompress every member.
            zf = _zip_open(archive, budget)
            by_name: Dict[str, Entry] = {}
            for entry in listing.entries:
                by_name.setdefault(entry.name, entry)
            for info in zf.infolist()[:MAX_SCAN_ENTRIES]:
                entry = by_name.get(info.orig_filename)
                if entry is None or _zip_kind(info)[0] != "file" or info.flag_bits & 0x1:
                    continue
                try:
                    for _chunk in zip_member_chunks(archive, info, budget):
                        pass
                    verified += 1
                except BudgetExceeded as exc:
                    stopped = str(exc)
                    break
                except ArchiveError as exc:
                    message = str(exc)
                    flag = ("crc-mismatch" if "CRC" in message
                            else "size-mismatch" if "declared" in message else "corrupt")
                    if flag not in entry.flags:
                        entry.flags.append(flag)
                    problems.append(f"{safe_name(info.orig_filename)}: {message}")
        elif listing.format == "tar" and not stopped:
            # A plain tar listing only read headers; read every member now.
            archive.raw.seek(0)
            work = _fresh(budget)
            try:
                for tar, info in iter_tar(archive, work):
                    if not info.isreg():
                        continue
                    produced = _drain(tar, info, work, charge=True)
                    if produced != info.size:
                        problems.append(f"{safe_name(info.name)}: {produced} of {info.size} bytes present")
                    verified += 1
            except BudgetExceeded as exc:
                stopped = str(exc)
            except ArchiveError as exc:
                problems.append(str(exc))
        elif not stopped:
            # Compressed tar (and single compressed files): listing already
            # decompressed the whole stream, whose own checksums (gzip CRC32,
            # bzip2 block CRCs, xz check) were verified by the decoder.
            verified = sum(1 for e in listing.entries if e.kind == "file")
        if stopped and not listing.stopped:
            listing.extra.append(f"UNSAFE {stopped}")
            listing.stopped = stopped
        if problems:
            listing.extra.append(f"UNSAFE {len(problems)} damaged member(s)")
        header, info = _summary(archive, listing)
    decompressed = budget.used + (work.used if work is not budget else 0)
    elapsed = time.perf_counter() - budget.started
    lines = [header, f"verified {verified} member(s); decompressed {decompressed} bytes in {elapsed:.2f}s"]
    lines.extend(f"problem: {problem}" for problem in problems[:50])
    flagged = [e for e in listing.entries if e.flags][:MAX_SHOWN_LINES]
    lines.extend(_entry_line(e) for e in flagged)
    output, _ = truncate("\n".join(lines), 12_000)
    return result("archive", output, action="check", path=archive.rel, verified=verified,
                  problems=problems[:200], decompressed_total=decompressed, **info,
                  flagged=[e.to_dict() for e in flagged])


def _drain(tar: tarfile.TarFile, info: tarfile.TarInfo, budget: Budget, *, charge: bool,
           keep: int = 0) -> Any:
    """Read a tar member in bounded chunks; return its size, or (bytes, size) with ``keep``."""
    handle = tar.extractfile(info)
    produced = 0
    kept: List[bytes] = []
    while handle is not None:
        data = handle.read(CHUNK)
        if not data:
            break
        produced += len(data)
        if charge:
            budget.charge(len(data))
        if keep and sum(map(len, kept)) < keep:
            kept.append(data)
        if keep and produced >= keep:
            break
    if keep:
        return b"".join(kept)[:keep], produced
    return produced


def _decode_member(data: bytes, name: str) -> str:
    if workspace.looks_binary(data):
        raise ArchiveError(f"member {safe_name(name)} is binary")
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return data.decode("utf-8", errors="replace")


def do_read(ctx: NativeSkillContext, user_path: str, member: str, max_chars: int) -> dict:
    budget = Budget()
    with OpenArchive(ctx, user_path) as archive:
        read = _read_member(archive, member, budget)
        rel = archive.rel
    data, member_size, duplicates = read.data, read.size, read.duplicates
    text = _decode_member(data, member)
    body, out_truncated = truncate(text, max_chars)
    cut = member_size is None or member_size > len(data)
    size_text = "size unknown" if member_size is None else f"{member_size} bytes"
    header = f"{rel} :: {safe_name(member)} ({size_text}" + (f", first {len(data)} read" if cut else "") + ")"
    if duplicates:
        header += f" [{duplicates + 1} members share this name; showing the last, which extraction keeps]"
    if read.note:
        header += f" [{read.note}]"
    return result("archive", f"{header}\n{body}", action="read", path=rel, member=safe_name(member),
                  size=member_size, text=body, truncated=cut or out_truncated, duplicates=duplicates,
                  note=read.note)


@dataclass
class MemberRead:
    data: bytes
    size: Optional[int]  # None: larger than the read cap, exact size unknown
    duplicates: int = 0
    note: str = ""


def _read_member(archive: OpenArchive, member: str, budget: Budget) -> MemberRead:
    if archive.format in {"zip", "unknown"}:
        zf = _zip_open(archive, budget)
        # Match the stored name (as listed), keeping the last entry of that
        # name, which is the one extractors end up writing.
        matches = [item for item in zf.infolist() if item.orig_filename == member]
        if not matches:
            raise ArchiveError(f"no member named {safe_name(member)!r}")
        info = matches[-1]
        kind = _zip_kind(info)[0]
        if kind != "file":
            raise ArchiveError(f"{safe_name(member)} is a {kind}, not a file")
        duplicates = len(matches) - 1
        data = b"".join(zip_member_chunks(archive, info, budget, MAX_MEMBER_BYTES))
        return MemberRead(data, info.file_size, duplicates)
    found: Optional[MemberRead] = None
    try:
        for tar, info in iter_tar(archive, budget):
            if info.name != member:
                continue
            if not info.isreg():
                raise ArchiveError(f"{safe_name(member)} is a {_tar_kind(info)}, not a file")
            # Keep scanning: extracting a tar writes members in order, so the
            # last member of a name is the one that ends up on disk.
            data, _ = _drain(tar, info, budget, charge=archive.format == "tar", keep=MAX_MEMBER_BYTES)
            found = MemberRead(data, info.size, found.duplicates + 1 if found else 0)
    except _NotTar:
        if archive.format == "tar":
            raise ArchiveError("not a readable tar archive") from None
        name = _single_stream_name(archive)
        if member != name:
            raise ArchiveError(f"this compressed file holds one member, {safe_name(name)!r}") from None
        archive.raw.seek(0)
        data = b"".join(_single_stream(archive, _fresh(budget), MAX_MEMBER_BYTES + 1))
        size = len(data) if len(data) <= MAX_MEMBER_BYTES else None
        return MemberRead(data[:MAX_MEMBER_BYTES], size)
    except BudgetExceeded as exc:
        if found is None:
            raise
        found.note = f"search for later members of the same name stopped: {exc}"
    except _StreamDamaged as exc:
        if found is None:
            raise
        found.note = f"WARNING: {exc}; the member's bytes were not covered by a valid stream checksum"
    if found is None:
        raise ArchiveError(f"no member named {safe_name(member)!r}")
    return found


def _execute(ctx: NativeSkillContext, tokens: List[str]) -> dict:
    action, args = tokens[0].lower(), tokens[1:]
    if action in {"list", "ls"}:
        opts = parse_options(args, valued=("max",))
        if len(opts.positionals) != 1:
            raise SkillUsageError("archive list <archive> [--max N]")
        return do_list(ctx, opts.positionals[0], opts.get_int("max", MAX_SHOWN_LINES, minimum=1, maximum=1000))
    if action in {"check", "verify"}:
        if len(args) != 1:
            raise SkillUsageError("archive check <archive>")
        return do_check(ctx, args[0])
    if action in {"read", "cat"}:
        opts = parse_options(args, valued=("max-chars",))
        if len(opts.positionals) != 2:
            raise SkillUsageError("archive read <archive> <member> [--max-chars N]")
        max_chars = opts.get_int("max-chars", DEFAULT_OUTPUT_CHARS, minimum=100, maximum=60_000)
        return do_read(ctx, opts.positionals[0], opts.positionals[1], max_chars)
    raise SkillUsageError(f"Unknown archive action {action!r}.\n{_USAGE}")


async def run(command: str, ctx: NativeSkillContext) -> dict:
    try:
        _, tokens = split_command(command)
        if not tokens or tokens[0].lower() in {"help", "--help"}:
            return result("archive", _USAGE, usage=True) if tokens else failure("archive", _USAGE)
        return await asyncio.to_thread(_execute, ctx, tokens)
    except (SkillUsageError, ArchiveError, workspace.WorkspaceError) as exc:
        return failure("archive", f"archive: {exc}")
