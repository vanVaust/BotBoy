#!/usr/bin/env python3
"""List FastAPI route decorators discovered in a BotBoy server file."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROUTE_RE = re.compile(r'@app\.(get|post|delete|websocket)\("([^"]+)"')


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Usage: route_matrix.py <server.py>", file=sys.stderr)
        return 2
    text = Path(argv[1]).read_text(encoding="utf-8")
    routes = [{"method": method.upper(), "path": path} for method, path in ROUTE_RE.findall(text)]
    print(json.dumps({"count": len(routes), "routes": routes}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
