#!/usr/bin/env python3
"""Summarize a BotBoy repo surface map."""

from __future__ import annotations

import json
import sys
from pathlib import Path


TARGETS = (
    "botboy/__main__.py",
    "botboy/gateway/server.py",
    "botboy/gateway/simple_server.py",
    "botboy_mcp_server.py",
    "web/index.html",
    "web/dashboard.html",
)


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path.cwd()
    report = {"root": str(root), "targets": []}
    for rel in TARGETS:
        path = root / rel
        report["targets"].append({"path": rel, "exists": path.exists(), "kind": "file" if path.is_file() else "missing"})
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
