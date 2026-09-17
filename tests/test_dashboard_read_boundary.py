import unittest

from botboy.gateway.app_context import _current_principal
from botboy.gateway import status_read_security as security


class DashboardReadBoundaryTest(unittest.TestCase):
    def test_authenticated_dashboard_removes_global_telemetry(self) -> None:
        original = security._original_dashboard_payload
        security._original_dashboard_payload = lambda _bot, mode="local": {
            "health": {"status": "healthy"},
            "history_stats": {"total": 99},
            "trace_summary": {"total_runs": 88},
            "metrics": {"total_commands": 77},
            "reflection_memory": {"total_entries": 66},
            "delegation": {"worker_count": 5},
            "a2a": {"total_adapters": 3},
            "operations_summary": {
                "task_total": 2,
                "trace_runs": 88,
                "history_total": 99,
                "metrics_total_commands": 77,
                "reflection_entry_count": 66,
                "delegation_worker_count": 5,
                "latest_task_id": "task-a",
            },
        }
        token = _current_principal.set("tenant-user")
        try:
            result = security._scoped_dashboard_payload(object())
        finally:
            _current_principal.reset(token)
            security._original_dashboard_payload = original

        self.assertIn("health", result)
        self.assertNotIn("history_stats", result)
        self.assertNotIn("trace_summary", result)
        self.assertNotIn("metrics", result)
        self.assertNotIn("reflection_memory", result)
        self.assertNotIn("delegation", result)
        self.assertNotIn("a2a", result)
        self.assertEqual(result["operations_summary"]["task_total"], 2)
        self.assertEqual(result["operations_summary"]["latest_task_id"], "task-a")
        for key in (
            "trace_runs", "history_total", "metrics_total_commands",
            "reflection_entry_count", "delegation_worker_count",
        ):
            self.assertNotIn(key, result["operations_summary"])
