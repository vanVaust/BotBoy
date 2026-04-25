#!/usr/bin/env python3
"""Validate that a dashboard payload exposes core Control Center fields."""

from __future__ import annotations

import json
import sys
from pathlib import Path


REQUIRED_PATHS = (
    ("snapshot",),
    ("traces",),
    ("monitoring",),
    ("control_center_contract",),
)

SNAPSHOT_KEYS = (
    "completed_capabilities",
    "open_priorities",
    "deferred_items",
    "eval_replay",
)

CONTRACT_KEYS = (
    "contract_version",
    "segments",
    "write_set",
)


def get_path(payload: dict, path: tuple[str, ...]):
    current = payload
    for key in path:
        if not isinstance(current, dict) or key not in current:
            raise KeyError(".".join(path))
        current = current[key]
    return current


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Usage: check_dashboard_contract.py <payload.json>", file=sys.stderr)
        return 2

    payload = json.loads(Path(argv[1]).read_text(encoding="utf-8-sig"))
    missing = []
    for path in REQUIRED_PATHS:
        try:
            get_path(payload, path)
        except KeyError:
            missing.append(".".join(path))

    snapshot = payload.get("snapshot", {})
    for key in SNAPSHOT_KEYS:
        if key not in snapshot:
            missing.append(f"snapshot.{key}")

    contract = payload.get("control_center_contract", {})
    for key in CONTRACT_KEYS:
        if key not in contract:
            missing.append(f"control_center_contract.{key}")

    segments = contract.get("segments", {}) if isinstance(contract, dict) else {}
    for key in ("operator_surface", "queue_lease", "replay", "incident"):
        if key not in segments:
            missing.append(f"control_center_contract.segments.{key}")

    result = {"ok": not missing, "missing": missing}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not missing else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
