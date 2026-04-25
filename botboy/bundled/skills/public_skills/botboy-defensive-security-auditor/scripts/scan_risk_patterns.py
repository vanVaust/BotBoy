#!/usr/bin/env python3
"""Scan a file or tree for risky code patterns."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


PATTERNS = {
    "dynamic_eval": re.compile(r"\b(eval|exec)\s*\("),
    "subprocess": re.compile(r"\bsubprocess\.\w+\s*\("),
    "os_system": re.compile(r"\bos\.system\s*\("),
    "open_write": re.compile(r"\bopen\s*\(.*['\"]w"),
    "network": re.compile(r"\b(requests|urllib|httpx|aiohttp)\b"),
}


def iter_files(path: Path):
    if path.is_file():
        yield path
        return
    for item in sorted(path.rglob("*")):
        if item.is_file():
            yield item


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Usage: scan_risk_patterns.py <path>", file=sys.stderr)
        return 2

    root = Path(argv[1])
    if not root.exists():
        print(json.dumps({"error": f"Path not found: {root}"}, indent=2))
        return 1

    matches = []
    for file_path in iter_files(root):
        try:
            text = file_path.read_text(encoding="utf-8-sig")
        except Exception:
            continue
        for name, pattern in PATTERNS.items():
            count = len(pattern.findall(text))
            if count:
                matches.append({"file": str(file_path), "pattern": name, "count": count})

    print(json.dumps({"root": str(root), "matches": matches, "total": len(matches)}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
