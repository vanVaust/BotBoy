#!/usr/bin/env python3
"""Emit a minimal virtual range experiment manifest."""

from __future__ import annotations

import argparse
import json


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    args = parser.parse_args()

    payload = {
        "experiment": args.name,
        "hypothesis": "",
        "isolation_boundary": "",
        "success_signals": [],
        "failure_signals": [],
        "rollback_plan": "",
        "promotion_decision": "undecided",
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
