#!/usr/bin/env python3
"""Filter the BotBoy omni skill catalog by phase, category, or text query."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_catalog(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def matches_query(skill: dict, query: str) -> bool:
    haystacks = [
        skill.get("name", ""),
        skill.get("summary", ""),
        " ".join(skill.get("tags", [])),
        skill.get("build_when", ""),
    ]
    lowered = query.lower()
    return any(lowered in item.lower() for item in haystacks)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", default=str(Path(__file__).resolve().parents[3] / "skill-catalog" / "botboy_omni_skill_catalog.json"))
    parser.add_argument("--phase", default="")
    parser.add_argument("--category", default="")
    parser.add_argument("--query", default="")
    args = parser.parse_args()

    catalog = load_catalog(Path(args.catalog))
    matches = []
    for category in catalog.get("categories", []):
        if args.category and category.get("id") != args.category:
            continue
        for skill in category.get("skills", []):
            if args.phase and skill.get("phase") != args.phase:
                continue
            if args.query and not matches_query(skill, args.query):
                continue
            matches.append(
                {
                    "category": category.get("id"),
                    "name": skill.get("name"),
                    "phase": skill.get("phase"),
                    "summary": skill.get("summary"),
                }
            )

    print(json.dumps({"count": len(matches), "matches": matches}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
