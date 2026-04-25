#!/usr/bin/env python3
"""Emit a bounded simulation scenario skeleton as JSON."""

from __future__ import annotations

import argparse
import json


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    args = parser.parse_args()

    payload = {
        "scenario": args.name,
        "actors": [],
        "state_variables": [],
        "allowed_actions": [],
        "signals": [],
        "transitions": [],
        "stopping_conditions": [],
        "success_criteria": [],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
