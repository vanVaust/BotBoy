from __future__ import annotations

import asyncio
import unittest

from botboy.security.replay_diff import (
    compare_replay_payloads,
    list_replay_diff_summaries,
    load_replay_diff_report,
    persist_replay_diff_report,
    summarize_replay_diffs,
)
from botboy.security.replay_harness import ReplayHarness
from botboy.tasks import TaskStore


class _TraceStore:
    def __init__(self, runs: dict[str, dict]) -> None:
        self._runs = runs

    def get_run(self, run_id: str) -> dict | None:
        return self._runs.get(run_id)


class _Bot:
    def __init__(self, task_store: TaskStore) -> None:
        self.task_store = task_store


class ReplayDiffTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = TaskStore(db_path=":memory:", artifact_root="")
        self.addCleanup(self.store.close)

    def test_task_store_migration_creates_replay_diff_table(self) -> None:
        rows = self.store._fetchall("SELECT name FROM sqlite_master WHERE type = 'table'")
        table_names = {row["name"] for row in rows}
        self.assertIn("replay_diffs", table_names)

    def test_compare_replay_payloads_reports_stable_reason_codes(self) -> None:
        expected = {
            "success": True,
            "run_id": "run-expected",
            "results": [
                {
                    "span_id": "span-1",
                    "simulated_component": "router",
                    "event_type": "command_started",
                    "status": "replayed",
                    "payload": {"route": "local"},
                }
            ],
        }
        actual = {
            "success": True,
            "run_id": "run-actual",
            "results": [
                {
                    "span_id": "span-1",
                    "simulated_component": "worker",
                    "event_type": "command_started",
                    "status": "blocked",
                    "payload": {"route": "remote"},
                }
            ],
        }

        report = compare_replay_payloads(expected, actual)
        codes = {entry.code for entry in report.entries}

        self.assertFalse(report.matches)
        self.assertIn("replay_component_changed", codes)
        self.assertIn("replay_status_changed", codes)
        self.assertIn("replay_payload_changed", codes)
        self.assertEqual(report.expected_run_id, "run-expected")
        self.assertEqual(report.actual_run_id, "run-actual")

    def test_persist_replay_diff_summary_roundtrip(self) -> None:
        report = compare_replay_payloads(
            {
                "success": True,
                "run_id": "run-1",
                "results": [{"span_id": "span-1", "event_type": "started", "status": "replayed"}],
            },
            {
                "success": False,
                "run_id": "run-2",
                "results": [{"span_id": "span-1", "event_type": "failed", "status": "failed"}],
            },
        )

        self.assertTrue(persist_replay_diff_report(self.store, report, source="unit_test"))
        loaded = load_replay_diff_report(self.store, report.report_id)
        summaries = list_replay_diff_summaries(self.store, run_id="run-1")
        aggregate = summarize_replay_diffs(self.store)

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["report_id"], report.report_id)
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["source"], "unit_test")
        self.assertEqual(aggregate["total"], 1)
        self.assertEqual(aggregate["drift_count"], 1)
        self.assertIn("replay_success_changed", aggregate["reason_code_counts"])

    def test_replay_harness_compare_runs_persists_report(self) -> None:
        trace_store = _TraceStore(
            {
                "run-a": {
                    "spans": [
                        {
                            "span_id": "span-1",
                            "component": "router",
                            "event_type": "command_started",
                            "payload_ref": "route=local",
                        }
                    ]
                },
                "run-b": {
                    "spans": [
                        {
                            "span_id": "span-1",
                            "component": "worker",
                            "event_type": "command_started",
                            "payload_ref": "route=worker",
                        }
                    ]
                },
            }
        )
        harness = ReplayHarness(trace_store, _Bot(self.store))

        result = asyncio.run(harness.compare_runs("run-a", "run-b", source="unit_test"))
        summary = summarize_replay_diffs(self.store)

        self.assertTrue(result["success"])
        self.assertTrue(result["persisted"])
        self.assertFalse(result["matches"])
        self.assertEqual(summary["total"], 1)
        self.assertIn("replay_component_changed", summary["reason_code_counts"])


if __name__ == "__main__":
    unittest.main()
