#!/usr/bin/env python3
"""Summarize likely deployable surfaces in a BotBoy repo."""

from __future__ import annotations

import json
import sys
from pathlib import Path


GROUPS = {
    "gateway": ("gateway", "server"),
    "skills": ("skills",),
    "memory": ("memory", "history"),
    "observability": ("trace", "monitor", "metrics"),
    "orchestration": ("planning", "__main__", "scheduler", "eval"),
}


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path.cwd()
    summary = {group: [] for group in GROUPS}
    for path in root.rglob("*.py"):
        lowered = str(path).lower()
        for group, keywords in GROUPS.items():
            if any(keyword in lowered for keyword in keywords):
                summary[group].append(str(path))
                break
    payload = {group: sorted(paths) for group, paths in summary.items()}
    payload["counts"] = {group: len(paths) for group, paths in summary.items()}
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
