#!/usr/bin/env python3
"""Append a starter case to a BotBoy eval manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, help="Path to manifest JSON")
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--command", required=True)
    parser.add_argument("--expect", required=True, help="Expected substring or marker")
    parser.add_argument("--category", default="regression")
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    cases = manifest.setdefault("cases", [])
    case = {
        "id": args.case_id,
        "category": args.category,
        "command": args.command,
        "checks": [{"type": "contains", "value": args.expect}],
    }
    cases.append(case)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(case, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
