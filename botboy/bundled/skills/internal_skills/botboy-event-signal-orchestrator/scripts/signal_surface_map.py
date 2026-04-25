#!/usr/bin/env python3
"""Inventory BotBoy files likely to host event or signal logic."""

from __future__ import annotations

import json
import sys
from pathlib import Path


KEYWORDS = ("event", "signal", "scheduler", "trace", "monitor", "approval", "trigger", "history")


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path.cwd()
    matches = []
    for path in root.rglob("*.py"):
        try:
            text = path.read_text(encoding="utf-8-sig").lower()
        except (OSError, UnicodeDecodeError):
            continue
        score = sum(text.count(keyword) for keyword in KEYWORDS)
        if score:
            matches.append({"path": str(path), "score": score})
    matches.sort(key=lambda item: (-item["score"], item["path"]))
    print(json.dumps({"count": len(matches), "top": matches[:25]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
