"""Run user-supplied regular expressions in a killable child process.

Python's ``re`` has no timeout, so a pattern such as ``(a+)+$`` against a
long non-matching subject can pin a CPU for minutes (ReDoS). Matching is
therefore done by a short, fixed, stdlib-only worker started as
``python -I -S -c <WORKER>`` — isolated mode, no site packages, no user
code on ``sys.path`` — with CPU and memory rlimits where the OS supports
them, and the parent kills it when the wall-clock budget is spent. User data
travels only through stdin as JSON; nothing is interpolated into the worker.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

MAX_PATTERN_CHARS = 1000
MAX_SUBJECT_BYTES = 16 * 1024 * 1024
_ALLOWED_FLAGS = {"i": "IGNORECASE", "m": "MULTILINE", "s": "DOTALL", "x": "VERBOSE", "a": "ASCII"}

_WORKER = r'''
import json, re, sys
try:
    import resource
    _cpu = int(sys.argv[1])
    resource.setrlimit(resource.RLIMIT_CPU, (_cpu, _cpu))
    _mem = 512 * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (_mem, _mem))
except Exception:
    pass
req = json.loads(sys.stdin.read())
flags = 0
for name in req["flags"]:
    flags |= getattr(re, name)
try:
    rx = re.compile(req["pattern"], flags)
except re.error as exc:
    print(json.dumps({"ok": False, "error": "Invalid pattern: %s" % exc}))
    sys.exit(0)
limit = req["max_matches"]
out = []
truncated = False
if req["mode"] == "lines":
    for doc_index, doc in enumerate(req["documents"]):
        for line_no, line in enumerate(doc.split("\n"), 1):
            m = rx.search(line)
            if m:
                out.append({"doc": doc_index, "line": line_no, "text": line[:400],
                            "start": m.start(), "end": m.end()})
                if len(out) >= limit:
                    truncated = True
                    break
        if truncated:
            break
else:
    subject = req["documents"][0]
    for m in rx.finditer(subject):
        out.append({"match": m.group(0)[:400], "start": m.start(), "end": m.end(),
                    "groups": [None if g is None else g[:200] for g in m.groups()],
                    "named": {k: (None if v is None else v[:200]) for k, v in m.groupdict().items()}})
        if len(out) >= limit:
            truncated = True
            break
print(json.dumps({"ok": True, "matches": out, "truncated": truncated, "groups": rx.groups}))
'''


class RegexSandboxError(ValueError):
    """Pattern rejected, invalid, or exceeded its budget."""


@dataclass
class RegexOutcome:
    matches: List[Dict[str, Any]] = field(default_factory=list)
    truncated: bool = False
    group_count: int = 0


def parse_flags(spec: str) -> List[str]:
    names: List[str] = []
    for char in (spec or "").lower():
        if char not in _ALLOWED_FLAGS:
            raise RegexSandboxError(f"Unknown regex flag {char!r}; allowed: {''.join(_ALLOWED_FLAGS)}")
        names.append(_ALLOWED_FLAGS[char])
    return names


def _child_env() -> Dict[str, str]:
    env = {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"}
    for key in ("PATH", "SystemRoot", "TEMP", "TMP"):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


async def run_regex(
    pattern: str,
    documents: Sequence[str],
    *,
    flags: Sequence[str] = (),
    mode: str = "finditer",
    max_matches: int = 100,
    timeout_seconds: float = 2.0,
) -> RegexOutcome:
    if not pattern:
        raise RegexSandboxError("Empty pattern")
    if len(pattern) > MAX_PATTERN_CHARS:
        raise RegexSandboxError(f"Pattern longer than {MAX_PATTERN_CHARS} characters")
    if mode not in {"finditer", "lines"}:
        raise RegexSandboxError(f"Unknown mode {mode!r}")
    payload = json.dumps({
        "pattern": pattern,
        "flags": list(flags),
        "documents": list(documents),
        "mode": mode,
        "max_matches": max_matches,
    }).encode("utf-8")
    if len(payload) > MAX_SUBJECT_BYTES:
        raise RegexSandboxError(f"Subject larger than {MAX_SUBJECT_BYTES} bytes")
    cpu_seconds = str(max(1, int(timeout_seconds) + 1))
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-I", "-S", "-c", _WORKER, cpu_seconds,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_child_env(),
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(payload), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise RegexSandboxError(
            f"Regex evaluation exceeded {timeout_seconds:g}s and was killed (catastrophic backtracking?)"
        ) from None
    if process.returncode != 0:
        detail = stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or ["no output"]
        raise RegexSandboxError(f"Regex worker failed (exit {process.returncode}): {detail[0][:200]}")
    try:
        response: Optional[Dict[str, Any]] = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RegexSandboxError("Regex worker returned malformed output") from exc
    if not response or not response.get("ok"):
        raise RegexSandboxError(str((response or {}).get("error", "Regex worker error")))
    return RegexOutcome(
        matches=list(response.get("matches", [])),
        truncated=bool(response.get("truncated")),
        group_count=int(response.get("groups", 0)),
    )
