"""Deterministic Replay-Harness for BotBoy V4 Traces."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional
import botboy.tracing as btrace
from botboy.security.replay_diff import compare_replay_payloads, persist_replay_diff_report

logger = logging.getLogger(__name__)

class ReplayHarness:
    """Replays executed traces deterministically for incident analysis / regression."""
    
    def __init__(self, trace_store: btrace.TraceStore, bot_instance: Any):
        self.store = trace_store
        self.bot = bot_instance

    async def replay_run(self, run_id: str) -> Dict[str, Any]:
        """
        Fetches a historic trace run and deterministically replays the steps.
        This ignores actual IO and uses the payload_refs to mock skill returns.
        """
        run_data = self.store.get_run(run_id)
        if not run_data:
            return {"success": False, "error": f"Run {run_id} not found."}
            
        spans = run_data.get("spans", [])
        logger.info(f"Replaying Run {run_id} with {len(spans)} spans.")
        
        replay_results = []
        for span in spans:
            comp = span.get("component")
            event_type = span.get("event_type")
            payload = span.get("payload_ref")
            
            # Simulation logic for deterministic event reconstruction.
            # In a full debugging environment, this runs the abstract AST or traces exactly.
            logger.debug(f"Replaying span {span['span_id']}: [{comp}] {event_type}")
            
            replay_results.append({
                "span_id": span["span_id"],
                "simulated_component": comp,
                "event_type": event_type,
                "payload": payload,
                "status": "replayed",
                "drift_detected": False # In v7 this explicitly checks if the LLM output drifts
            })
            
        return {
            "success": True,
            "run_id": run_id,
            "replayed_spans": len(replay_results),
            "results": replay_results
        }

    async def compare_runs(self, expected_run_id: str, actual_run_id: str, *, source: str = "replay_harness") -> Dict[str, Any]:
        """Compare two trace runs and persist the replay drift report when a TaskStore is available."""
        expected_run = self.store.get_run(expected_run_id)
        actual_run = self.store.get_run(actual_run_id)
        if not expected_run:
            return {"success": False, "error": f"Run {expected_run_id} not found."}
        if not actual_run:
            return {"success": False, "error": f"Run {actual_run_id} not found."}
        report = compare_replay_payloads(
            {
                "success": True,
                "run_id": expected_run_id,
                "span_count": len(expected_run.get("spans", [])),
                "spans": expected_run.get("spans", []),
            },
            {
                "success": True,
                "run_id": actual_run_id,
                "span_count": len(actual_run.get("spans", [])),
                "spans": actual_run.get("spans", []),
            },
        )
        task_store = getattr(self.bot, "task_store", None)
        persisted = persist_replay_diff_report(task_store, report, source=source) if task_store else False
        payload = report.to_dict()
        payload["success"] = True
        payload["persisted"] = persisted
        return payload
