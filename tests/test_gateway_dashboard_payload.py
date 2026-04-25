from __future__ import annotations

import unittest

from botboy.gateway.dashboard_payload import enrich_dashboard_payload


class _FakeRecord:
    def __init__(self, task_id: str, root_task_id: str = "root") -> None:
        self.task_id = task_id
        self.root_task_id = root_task_id


class GatewayDashboardPayloadTest(unittest.TestCase):
    def test_enrich_dashboard_payload_with_store(self) -> None:
        latest = _FakeRecord("task-1")
        child = _FakeRecord("task-2")
        payload = {
            "tasks": {"latest": {"task_id": "task-1"}, "total": 1},
            "operations_summary": {},
        }
        result = enrich_dashboard_payload(
            payload,
            store=object(),
            metrics={
                "blocked_count": 1,
                "delegated_count": 2,
                "running_count": 3,
                "queued_count": 4,
                "worker_count": 5,
                "handoff_queue_depth": 6,
                "oldest_blocked_age_s": 7,
                "oldest_running_age_s": 8,
                "by_worker": {"planner": 1},
                "by_blocked_kind": {"waiting": 1},
                "blockers": [{"task_id": "task-1"}],
                "recent_blockers": [{"task_id": "task-1"}],
            },
            workers={
                "available": True,
                "worker_count": 1,
                "registry": [{"worker_id": "planner"}],
            },
            get_task=lambda task_id: latest if task_id == "task-1" else child if task_id == "task-2" else None,
            task_records_by_root=lambda _store, _root_task_id: [latest, child],
            direct_child_records=lambda records, parent_task_id: [record for record in records if record.task_id != parent_task_id],
            decorate_task_record=lambda _store, record, root_records=None: {
                "task_id": record.task_id,
                "decorated": True,
                "root_count": len(root_records or []),
            },
            build_task_graph=lambda _store, records, focus_task_id: {
                "available": True,
                "focus_task_id": focus_task_id,
                "node_count": len(records),
            },
        )

        self.assertTrue(result["workers"]["available"])
        self.assertEqual(result["tasks"]["latest"]["task_id"], "task-1")
        self.assertEqual(result["tasks"]["children"][0]["task_id"], "task-2")
        self.assertEqual(result["operations_summary"]["worker_count"], 5)
        self.assertEqual(result["handoffs"]["graph"]["focus_task_id"], "task-1")
        self.assertIn("control_center_contract", result)
        contract = result["control_center_contract"]
        self.assertEqual(contract["segment_order"], ["operator_surface", "queue_lease", "replay", "incident"])
        queue_runtime = contract["segments"]["queue_lease"]["runtime"]
        self.assertTrue(queue_runtime["available"])
        self.assertEqual(queue_runtime["handoff_queue_depth"], 6)
        incident_runtime = contract["segments"]["incident"]["runtime"]
        self.assertTrue(incident_runtime["available"])
        self.assertEqual(incident_runtime["blocked_count"], 1)

    def test_enrich_dashboard_payload_without_store(self) -> None:
        payload = {}
        result = enrich_dashboard_payload(
            payload,
            store=None,
            metrics={},
            workers={},
            get_task=lambda _task_id: None,
            task_records_by_root=lambda _store, _root_task_id: [],
            direct_child_records=lambda records, parent_task_id: [],
            decorate_task_record=lambda _store, record, root_records=None: {},
            build_task_graph=lambda _store, records, focus_task_id: {},
        )

        self.assertEqual(result["workers"]["worker_count"], 5)
        self.assertFalse(result["handoffs"]["available"])
        self.assertIn("control_center_contract", result)
        self.assertIn("contract_version", result["control_center_contract"])
        self.assertIn("segments", result["control_center_contract"])


if __name__ == "__main__":
    unittest.main()
