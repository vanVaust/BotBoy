#!/usr/bin/env python3
"""Summarize crypto-sensitive files in a BotBoy workspace."""

from __future__ import annotations

import json
import sys
from pathlib import Path


KEYWORDS = ("crypto", "token", "secret", "signature", "jwt", "hmac", "replay", "encrypt", "decrypt", "key")


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path.cwd()
    hits = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".py", ".md", ".json", ".yaml", ".yml"}:
            continue
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            continue
        count = sum(text.lower().count(word) for word in KEYWORDS)
        if count:
            hits.append({"path": str(path), "score": count})
    hits.sort(key=lambda item: (-item["score"], item["path"]))
    print(json.dumps({"count": len(hits), "top": hits[:20]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
