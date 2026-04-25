from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any


RUNBOOK_PATH = Path(__file__).resolve().parents[1] / "botboy" / "data" / "REMOTE_AUTH_RUNBOOK.json"
REQUIRED_CHECK_DOMAINS = {"secrets", "cors", "mcp", "worker_fabric"}
REQUIRED_PHASE_IDS = {
    "phase_0_inventory",
    "phase_1_secret_preflight",
    "phase_2_gateway_cors_preflight",
    "phase_3_mcp_preflight",
    "phase_4_worker_fabric_preflight",
    "phase_5_go_live_monitor",
}
REQUIRED_RUNBOOK_CHECKS = {
    "gateway_remote_auth",
    "jwt_secret",
    "bootstrap_principal",
    "principal_store",
    "api_key_store",
    "cors_policy",
    "cors_operator_origin_inventory",
    "mcp_http_token",
    "mcp_remote_bind_scope",
    "mcp_tool_approval",
    "worker_node_registry",
    "worker_queue_registry",
    "worker_heartbeat_freshness",
    "worker_lease_recovery",
    "worker_drain_path",
    "worker_result_auth",
}
PLACEHOLDER_MARKERS = (
    "TODO",
    "TBD",
    "FIXME",
    "CHANGEME",
    "PLACEHOLDER",
    "example.com",
    "<",
    ">",
)


def _load_runbook() -> dict[str, Any]:
    return json.loads(RUNBOOK_PATH.read_text(encoding="utf-8"))


def _walk_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        strings: list[str] = []
        for child in value.values():
            strings.extend(_walk_strings(child))
        return strings
    if isinstance(value, list):
        strings = []
        for child in value:
            strings.extend(_walk_strings(child))
        return strings
    return []


class RemoteAuthRunbookTest(unittest.TestCase):
    def test_runbook_is_valid_json_with_required_top_level_sections(self) -> None:
        runbook = _load_runbook()

        self.assertEqual(runbook["schema_version"], "1.0")
        self.assertEqual(runbook["runbook_id"], "botboy.remote_auth_operator_rollout")
        for key in ("metadata", "applicability", "invariants", "phases", "gates", "checks", "rollback"):
            self.assertIn(key, runbook)
            self.assertTrue(runbook[key])

    def test_runbook_has_ordered_phases_with_gates_and_rollbacks(self) -> None:
        runbook = _load_runbook()
        phases = runbook["phases"]
        gates_by_id = {gate["id"]: gate for gate in runbook["gates"]}
        rollback_ids = {item["id"] for item in runbook["rollback"]}

        self.assertEqual({phase["id"] for phase in phases}, REQUIRED_PHASE_IDS)
        self.assertEqual([phase["order"] for phase in phases], sorted(phase["order"] for phase in phases))

        for phase in phases:
            self.assertIn(phase["gate_id"], gates_by_id)
            self.assertEqual(gates_by_id[phase["gate_id"]]["phase_id"], phase["id"])
            self.assertTrue(phase["actions"])
            self.assertTrue(phase["rollback_ids"])
            self.assertTrue(set(phase["rollback_ids"]).issubset(rollback_ids))

    def test_required_check_domains_and_ids_are_covered(self) -> None:
        runbook = _load_runbook()
        checks_by_domain = runbook["checks"]

        self.assertEqual(set(checks_by_domain), REQUIRED_CHECK_DOMAINS)

        checks_by_id: dict[str, dict[str, Any]] = {}
        for domain, checks in checks_by_domain.items():
            self.assertTrue(checks, domain)
            for check in checks:
                self.assertNotIn(check["id"], checks_by_id)
                self.assertIn(check["phase_id"], REQUIRED_PHASE_IDS)
                self.assertIn(check["severity"], {"blocker", "warning"})
                self.assertTrue(check.get("command") or check.get("inspection"))
                self.assertTrue(check["pass_criteria"])
                self.assertTrue(check["fail_action"])
                self.assertTrue(check["evidence"])
                checks_by_id[check["id"]] = check

        self.assertTrue(REQUIRED_RUNBOOK_CHECKS.issubset(checks_by_id))

    def test_gate_required_checks_exist_and_block_rollout_on_failure(self) -> None:
        runbook = _load_runbook()
        checks_by_id = {
            check["id"]
            for checks in runbook["checks"].values()
            for check in checks
        }

        for gate in runbook["gates"]:
            self.assertTrue(gate["required_check_ids"])
            self.assertTrue(set(gate["required_check_ids"]).issubset(checks_by_id))
            self.assertTrue(gate["evidence_required"])
            self.assertIn(gate["decision"], {"continue", "operate"})
            self.assertIn("failure_mode", gate)

    def test_rollback_entries_are_actionable(self) -> None:
        runbook = _load_runbook()

        for entry in runbook["rollback"]:
            self.assertTrue(entry["id"].startswith("rollback_"))
            self.assertTrue(entry["triggers"])
            self.assertTrue(entry["actions"])
            self.assertTrue(entry["verification"])

    def test_runbook_contains_no_placeholder_markers(self) -> None:
        runbook = _load_runbook()
        strings = _walk_strings(runbook)

        offenders = [
            value
            for value in strings
            if any(marker in value.upper() for marker in PLACEHOLDER_MARKERS[:5])
            or any(marker in value for marker in PLACEHOLDER_MARKERS[5:])
        ]

        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
