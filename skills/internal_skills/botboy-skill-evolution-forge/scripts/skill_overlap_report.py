#!/usr/bin/env python3
"""Flag likely overlap across skill names and descriptions."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def read_frontmatter_text(path: Path) -> str:
    text = path.read_text(encoding="utf-8-sig")
    if not text.startswith("---"):
        return ""
    parts = text.split("---", 2)
    return parts[1] if len(parts) > 2 else ""


def extract_field(frontmatter: str, name: str) -> str:
    match = re.search(rf"^{name}:\s*(.+)$", frontmatter, flags=re.MULTILINE)
    return match.group(1).strip().strip("'\"") if match else ""


def main(argv: list[str]) -> int:
    base = Path(argv[1]) if len(argv) > 1 else Path.cwd()
    skills = []
    for skill_md in sorted(base.glob("*/SKILL.md")):
        fm = read_frontmatter_text(skill_md)
        skills.append(
            {
                "path": str(skill_md),
                "name": extract_field(fm, "name"),
                "description": extract_field(fm, "description"),
            }
        )

    overlaps = []
    for i, left in enumerate(skills):
        left_words = set(left["name"].split("-"))
        for right in skills[i + 1 :]:
            right_words = set(right["name"].split("-"))
            shared = sorted(word for word in (left_words & right_words) if word and word != "botboy")
            if len(shared) >= 2:
                overlaps.append({"left": left["name"], "right": right["name"], "shared_words": shared})

    print(json.dumps({"skills": len(skills), "overlaps": overlaps}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
