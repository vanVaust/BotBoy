#!/usr/bin/env python3
"""Audit a skill library for core artifact completeness."""

from __future__ import annotations

import json
import sys
from pathlib import Path


REQUIRED = (
    "SKILL.md",
    "agents/openai.yaml",
)


def iter_skill_dirs(base: Path):
    for child in sorted(base.iterdir()):
        if child.is_dir():
            yield child


def main(argv: list[str]) -> int:
    base = Path(argv[1]) if len(argv) > 1 else Path.cwd()
    if not base.exists():
        print(json.dumps({"error": f"Path not found: {base}"}, indent=2))
        return 1

    report = {"base": str(base), "skills": [], "totals": {"skills": 0, "missing": 0}}
    for skill_dir in iter_skill_dirs(base):
        if not (skill_dir / "SKILL.md").exists():
            continue
        missing = [rel for rel in REQUIRED if not (skill_dir / rel).exists()]
        item = {
            "name": skill_dir.name,
            "has_scripts": (skill_dir / "scripts").is_dir(),
            "has_references": (skill_dir / "references").is_dir(),
            "missing": missing,
        }
        report["skills"].append(item)
        report["totals"]["skills"] += 1
        if missing:
            report["totals"]["missing"] += 1

    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
