#!/usr/bin/env python3
"""Summarize a saved trace or dashboard JSON artifact."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def summarize(payload: dict) -> dict:
    traces = payload.get("traces", payload)
    summary = {
        "available": traces.get("available"),
        "total_runs": traces.get("total_runs"),
        "total_spans": traces.get("total_spans"),
        "by_status": traces.get("by_status", {}),
    }
    if "run_id" in traces:
        summary["run_id"] = traces.get("run_id")
    if "trace_id" in traces:
        summary["trace_id"] = traces.get("trace_id")
    if "principal" in traces:
        summary["principal"] = traces.get("principal")
    return summary


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Usage: trace_report.py <json-path>", file=sys.stderr)
        return 2
    payload = json.loads(Path(argv[1]).read_text(encoding="utf-8-sig"))
    print(json.dumps(summarize(payload), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
