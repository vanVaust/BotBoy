#!/usr/bin/env python3
"""Check a simple BotBoy fact-and-rule model for unmet invariants."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def normalize(values):
    return {str(item).strip() for item in values if str(item).strip()}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Usage: check_invariants.py <model.json>", file=sys.stderr)
        return 2

    model_path = Path(argv[1])
    model = json.loads(model_path.read_text(encoding="utf-8-sig"))
    facts = normalize(model.get("facts", []))
    rules = model.get("rules", [])
    violations = []

    for index, rule in enumerate(rules, start=1):
        required = normalize(rule.get("if", []))
        implied = normalize(rule.get("then", []))
        if required and required.issubset(facts):
            continue
        missing = sorted(required - facts)
        if missing:
            violations.append({
                "rule": index,
                "missing": missing,
                "then": sorted(implied),
            })

    contradictions = []
    for pair in model.get("contradictions", []):
        left = str(pair[0]).strip()
        right = str(pair[1]).strip()
        if left in facts and right in facts:
            contradictions.append([left, right])

    report = {
        "facts": sorted(facts),
        "violations": violations,
        "contradictions": contradictions,
        "ok": not violations and not contradictions,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
