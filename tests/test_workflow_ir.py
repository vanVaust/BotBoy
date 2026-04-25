from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path

from botboy.evals import EvalReplayRunner
from botboy.workflow_ir import (
    WorkflowReplayRecorder,
    build_policy_decision,
    compile_workflow_ir,
    workflow_step_from_spec,
)


ROOT = Path(__file__).resolve().parents[1]


class WorkflowIRTest(unittest.TestCase):
    def test_compile_validate_and_roundtrip(self) -> None:
        workflow = compile_workflow_ir(
            goal="ship release",
            principal="release.test",
            request_id="req-1",
            step_specs=[
                {"step_id": "plan", "command": "status"},
                {"step_id": "verify", "command": "help", "depends_on": ["plan"]},
            ],
        )

        self.assertFalse(workflow.validate())
        payload = workflow.to_dict()
        self.assertEqual(payload["version"], "workflow-ir/v1")
        self.assertEqual(payload["steps"][1]["depends_on"], ["plan"])
        self.assertEqual(compile_workflow_ir(goal="ship release", principal="release.test", request_id="req-1", step_specs=[]).workflow_id[:3], "wf-")

    def test_validation_finds_missing_dependency_and_cycle(self) -> None:
        workflow = compile_workflow_ir(
            goal="bad flow",
            step_specs=[
                {"step_id": "a", "command": "status", "depends_on": ["b"]},
                {"step_id": "b", "command": "help", "depends_on": ["a"]},
                {"step_id": "c", "command": "skills", "depends_on": ["missing"]},
            ],
        )

        issue_codes = {issue.code for issue in workflow.validate()}
        self.assertIn("cyclic_dependency", issue_codes)
        self.assertIn("missing_dependency", issue_codes)

    def test_policy_decision_requires_approval_for_risky_command(self) -> None:
        step = workflow_step_from_spec({"step_id": "danger", "command": "memory delete secrets"}, index=0)

        denied = build_policy_decision(
            workflow_id="wf-1",
            step=step,
            principal="user",
            approval_context={},
        )
        allowed = build_policy_decision(
            workflow_id="wf-1",
            step=step,
            principal="admin",
            roles=["admin"],
            approval_context={},
        )

        self.assertFalse(denied.allowed)
        self.assertTrue(denied.approval_required)
        self.assertTrue(allowed.allowed)
        self.assertEqual(allowed.reason, "approval_granted")

    def test_replay_recorder_is_append_only(self) -> None:
        recorder = WorkflowReplayRecorder("wf-1", clock=lambda: "2026-04-23T00:00:00+00:00")

        recorder.record("workflow_started", status="running")
        recorder.record("step_completed", step_id="s1", status="success", payload={"result_success": True})

        events = recorder.to_dicts()
        self.assertEqual([event["event_type"] for event in events], ["workflow_started", "step_completed"])
        self.assertEqual(events[0]["created_at"], "2026-04-23T00:00:00+00:00")
        self.assertNotEqual(events[0]["event_id"], events[1]["event_id"])


class WorkflowIREvalReplayTest(unittest.TestCase):
    def test_workflow_eval_observes_ir_policy_decisions_and_replay_events(self) -> None:
        manifest = {
            "name": "workflow_ir_test",
            "wave": "unit",
            "cases": [
                {
                    "id": "workflow_ir_case",
                    "kind": "workflow",
                    "expected_contains": ["Status"],
                    "expected": {
                        "steps": [
                            {"step_id": "status", "command": "status"},
                            {"step_id": "help", "command": "help", "depends_on": ["status"]},
                        ],
                        "required_paths": [
                            "workflow_ir.workflow_id",
                            "workflow_ir.steps.0.command",
                            "policy_decisions.0.allowed",
                            "replay_events.0.event_type",
                        ],
                        "field_values": {
                            "workflow_ir.steps.0.command": "status",
                            "policy_decisions.0.allowed": True,
                            "replay_events.0.event_type": "workflow_started",
                        },
                        "min_step_count": 2,
                        "min_trace_runs": 2,
                    },
                }
            ],
        }
        seed = {
            "id": "workflow-ir-seed",
            "case_id": "workflow_ir_case",
            "kind": "workflow",
            "principal": "workflow.test",
            "request_id": "workflow-ir-req",
            "input": "status",
            "expected_contains": ["Status"],
        }

        root = ROOT / ".botboy-runtime" / self._testMethodName
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        manifest_path = root / "manifest.json"
        seed_path = root / "seed.jsonl"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        seed_path.write_text(json.dumps(seed) + "\n", encoding="utf-8")

        result = EvalReplayRunner(manifest_path, seed_path).run()

        self.assertEqual(result.failed, 0, result.render())
        self.assertEqual(result.passed, 1)


if __name__ == "__main__":
    unittest.main()
