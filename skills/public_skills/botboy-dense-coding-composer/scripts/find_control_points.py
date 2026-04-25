#!/usr/bin/env python3
"""Find files with concentrated occurrences of a search term."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("Usage: find_control_points.py <repo-root> <term>", file=sys.stderr)
        return 2
    root = Path(argv[1])
    term = argv[2].lower()
    hits = []
    for path in root.rglob("*.py"):
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            continue
        count = text.lower().count(term)
        if count:
            hits.append({"path": str(path), "count": count})
    hits.sort(key=lambda item: (-item["count"], item["path"]))
    print(json.dumps({"term": term, "hits": hits[:20]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
