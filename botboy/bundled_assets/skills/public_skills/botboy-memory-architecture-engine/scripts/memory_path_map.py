#!/usr/bin/env python3
"""Enumerate memory-related Python modules in the BotBoy repo."""

from __future__ import annotations

import json
import sys
from pathlib import Path


KEYWORDS = ("memory", "history", "reflection", "context", "trace")


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path.cwd()
    matches = []
    for path in root.rglob("*.py"):
        lowered = str(path).lower()
        if any(keyword in lowered for keyword in KEYWORDS):
            matches.append(str(path))
    print(json.dumps({"count": len(matches), "paths": sorted(matches)}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
