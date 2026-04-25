#!/usr/bin/env python3
"""Generate a concise BotBoy handoff brief."""

from __future__ import annotations

import argparse


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--objective", required=True)
    parser.add_argument("--current-state", required=True)
    parser.add_argument("--verification", default="")
    parser.add_argument("--risk", action="append", default=[])
    parser.add_argument("--next-step", required=True)
    parser.add_argument("--audience", default="agent")
    args = parser.parse_args()

    lines = [
        f"Audience: {args.audience}",
        f"Objective: {args.objective}",
        f"Current state: {args.current_state}",
    ]
    if args.verification:
        lines.append(f"Verification: {args.verification}")
    if args.risk:
        lines.append("Risk: " + "; ".join(args.risk))
    lines.append(f"Next step: {args.next_step}")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
