#!/usr/bin/env python3
"""Print a BotBoy runtime policy decision for a simple risk profile."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from botboy.skills.runtime import resolve_runtime_policy
except Exception:  # pragma: no cover - fallback path
    resolve_runtime_policy = None


def local_fallback(args: argparse.Namespace) -> dict:
    runtime_name = "inprocess" if args.trusted and args.security_level == "BEGINNER" else "subprocess"
    if args.security_level in {"ADVANCED", "EXPERT"}:
        runtime_name = "isolated"
    if args.allow_network or args.allow_filesystem:
        runtime_name = "subprocess" if runtime_name == "inprocess" else runtime_name
    needs_approval = bool(args.allow_network or args.allow_filesystem or not args.trusted or args.needs_approval)
    return {
        "runtime_name": runtime_name,
        "needs_approval": needs_approval,
        "reason": "local-fallback",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--security-level", default="INTERMEDIATE")
    parser.add_argument("--trusted", action="store_true")
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--allow-filesystem", action="store_true")
    parser.add_argument("--needs-approval", action="store_true")
    parser.add_argument("--approval-granted", action="store_true")
    parser.add_argument("--is-admin", action="store_true")
    args = parser.parse_args()

    if resolve_runtime_policy is not None:
        decision = resolve_runtime_policy(
            security_level=args.security_level,
            runtime_tier="auto",
            trusted=args.trusted,
            allow_network=args.allow_network,
            allow_filesystem=args.allow_filesystem,
            needs_approval=args.needs_approval,
            approval_granted=args.approval_granted,
            roles=["admin"] if args.is_admin else [],
        )
        payload = dict(decision.to_dict())
        payload["reason"] = "botboy-runtime"
    else:
        payload = local_fallback(args)

    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
