#!/usr/bin/env python3
"""Summarize a BotBoy isolation decision for a risk profile."""

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
except Exception:  # pragma: no cover
    resolve_runtime_policy = None


def fallback(args: argparse.Namespace) -> dict:
    runtime_name = "subprocess"
    if args.security_level.upper() in {"ADVANCED", "EXPERT"}:
        runtime_name = "isolated"
    if args.trusted and args.security_level.upper() == "BEGINNER" and not args.allow_network and not args.allow_filesystem:
        runtime_name = "inprocess"
    needs_approval = bool(args.needs_approval or not args.trusted or args.allow_network or args.allow_filesystem)
    return {"allowed": not needs_approval, "runtime_name": runtime_name, "needs_approval": needs_approval, "reason": "local-fallback"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--security-level", default="INTERMEDIATE")
    parser.add_argument("--runtime-tier", default="auto")
    parser.add_argument("--trusted", action="store_true")
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--allow-filesystem", action="store_true")
    parser.add_argument("--needs-approval", action="store_true")
    parser.add_argument("--approval-granted", action="store_true")
    parser.add_argument("--roles", default="")
    args = parser.parse_args()

    roles = [item.strip() for item in args.roles.split(",") if item.strip()]
    if resolve_runtime_policy is not None:
        decision = resolve_runtime_policy(
            security_level=args.security_level,
            runtime_tier=args.runtime_tier,
            trusted=args.trusted,
            allow_network=args.allow_network,
            allow_filesystem=args.allow_filesystem,
            needs_approval=args.needs_approval,
            approval_granted=args.approval_granted,
            roles=roles,
        )
        payload = dict(decision.to_dict())
        payload["reason"] = "botboy-runtime"
    else:
        payload = fallback(args)

    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
