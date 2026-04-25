#!/usr/bin/env python3
"""Summarize a state machine JSON model."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Usage: state_machine_summary.py <json-model>", file=sys.stderr)
        return 2
    model = json.loads(Path(argv[1]).read_text(encoding="utf-8-sig"))
    states = model.get("states", [])
    transitions = model.get("transitions", [])
    summary = {
        "state_count": len(states),
        "transition_count": len(transitions),
        "start_state": model.get("start_state", ""),
        "accepting_states": model.get("accepting_states", []),
        "states": states,
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
