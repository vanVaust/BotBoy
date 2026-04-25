#!/usr/bin/env python3
"""Scan a file or repository tree for defensive risk patterns."""

from __future__ import annotations

import json
import sys
from pathlib import Path


PATTERNS = (
    "eval",
    "exec",
    "subprocess",
    "os.system",
    "open(",
    "path traversal",
    "prompt injection",
    "http://",
    "https://",
)


def iter_files(root: Path):
    if root.is_file():
        yield root
        return
    for path in root.rglob("*.py"):
        yield path


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Usage: scan_risk_patterns.py <path>", file=sys.stderr)
        return 2
    root = Path(argv[1])
    results = []
    for path in iter_files(root):
        try:
            text = path.read_text(encoding="utf-8-sig").lower()
        except (OSError, UnicodeDecodeError):
            continue
        hits = [pattern for pattern in PATTERNS if pattern in text]
        if hits:
            results.append({"path": str(path), "hits": hits})
    print(json.dumps({"count": len(results), "results": results[:50]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
