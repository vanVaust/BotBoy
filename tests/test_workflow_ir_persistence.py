from __future__ import annotations

import asyncio
import shutil
import sqlite3
import unittest
from pathlib import Path

from botboy.command_execution_service import CommandExecutionService
from botboy.tasks import TASK_STATUS_COMPLETED, TASK_STATUS_FAILED, TaskStore
from botboy.workflow_ir import (
    WorkflowReplayRecorder,
    build_policy_decision,
    compile_workflow_ir,
)
from botboy.workflow_ir_persistence import (
    load_workflow_ir_snapshot,
    persist_workflow_ir_snapshot,
)


class _RuntimeBot:
    def __init__(self, task_store: TaskStore) -> None:
        self.task_store = task_store
        self.cache = None
        self.skills = None
        self.validator = None
        self.metrics = None
        self.trace_store = None
        self.archetypes = None
        self.reflection = None
        self.llm = None
        self.monitor = None
        self.history = None

    def _create_task_context(self, command: str, **kwargs):
        return self.task_store.create_task(
            title=command[:64] or "command",
            command=command,
            principal=str(kwargs.get("principal", "anonymous") or "anonymous"),
            request_id=str(kwargs.get("request_id", "") or ""),
            status="queued",
            owner="botboy",
            payload={},
        )

    def _start_trace_run(self, *_args):
        return None, None, None

    async def _route(self, command: str, **_kwargs):
        return {"success": True, "output": f"ok:{command}", "type": "status"}

    def _decorate_with_task(self, result: dict, task_ctx, status: str) -> dict:
        payload = dict(result or {})
        data = dict(payload.get("data", {}))
        data["task"] = {"task_id": task_ctx.task_id, "status": status}
        payload["data"] = data
        return payload

    def _finish_trace_run(self, *_args, **_kwargs) -> None:
        return None

    def _infer_task_status(self, result: dict) -> str:
        return TASK_STATUS_COMPLETED if result.get("success", False) else TASK_STATUS_FAILED


class WorkflowIRPersistenceTest(unittest.TestCase):
    def setUp(self) -> None:
        runtime_root = (Path(__file__).resolve().parents[1] / ".botboy-runtime" / self._testMethodName).resolve()
        shutil.rmtree(runtime_root, ignore_errors=True)
        runtime_root.mkdir(parents=True, exist_ok=True)
        self.db_path = runtime_root / "tasks.db"
        self.artifact_root = runtime_root / "artifacts" / "tasks"
        self.store = TaskStore(db_path=str(self.db_path), artifact_root=str(self.artifact_root))
        self.addCleanup(self.store.close)
        self.addCleanup(lambda: shutil.rmtree(runtime_root, ignore_errors=True))

    def test_task_store_migration_creates_workflow_ir_tables(self) -> None:
        conn = sqlite3.connect(str(self.db_path))
        self.addCleanup(conn.close)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name ASC"
        ).fetchall()
        table_names = {row[0] for row in rows}

        self.assertIn("workflow_runs", table_names)
        self.assertIn("workflow_policy_decisions", table_names)
        self.assertIn("workflow_replay_events", table_names)

    def test_persist_workflow_ir_snapshot_roundtrip(self) -> None:
        workflow = compile_workflow_ir(
            goal="status",
            principal="workflow.test",
            request_id="req-workflow",
            workflow_id="wf-live-test",
            step_specs=[{"step_id": "s1", "command": "status"}],
            metadata={"hook": "unit"},
        )
        decision = build_policy_decision(
            workflow_id=workflow.workflow_id,
            step=workflow.steps[0],
            principal=workflow.principal,
            roles=["operator"],
            approval_context={"granted": True},
            surface="live_runtime_command",
        )
        workflow.policy_decisions = [decision]
        replay = WorkflowReplayRecorder(workflow.workflow_id, clock=lambda: "2026-04-23T00:00:00+00:00")
        replay.record("workflow_started", status="running")
        replay.record("step_completed", step_id="s1", status="completed", payload={"result_success": True})
        replay.record("workflow_completed", status="completed", payload={"task_status": "completed"})

        persisted = persist_workflow_ir_snapshot(
            self.store,
            workflow_ir=workflow,
            policy_decisions=[decision],
            replay_events=replay.events,
            task_id="task-workflow-1",
            request_id="req-workflow",
            principal="workflow.test",
            source="unit_test",
            status="completed",
        )
        loaded = load_workflow_ir_snapshot(self.store, workflow.workflow_id)

        self.assertTrue(persisted)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded["workflow_id"], workflow.workflow_id)
        self.assertEqual(loaded["task_id"], "task-workflow-1")
        self.assertEqual(loaded["source"], "unit_test")
        self.assertEqual(loaded["status"], "completed")
        self.assertEqual(len(loaded["policy_decisions"]), 1)
        self.assertEqual(len(loaded["replay_events"]), 3)
        self.assertEqual(loaded["workflow_ir"]["steps"][0]["command"], "status")

    def test_command_execution_service_persists_live_workflow_snapshot(self) -> None:
        bot = _RuntimeBot(self.store)
        result = asyncio.run(
            CommandExecutionService(bot).process_command(
                "status",
                principal="runtime.test",
                request_id="req-live-1",
                roles=["operator"],
                approval_context={"granted": True, "source": "unit"},
            )
        )

        self.assertTrue(result["success"])
        task_id = result["data"]["task"]["task_id"]
        workflow_id = f"wf-live-{task_id}"
        loaded = load_workflow_ir_snapshot(self.store, workflow_id)

        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded["task_id"], task_id)
        self.assertEqual(loaded["request_id"], "req-live-1")
        self.assertEqual(loaded["principal"], "runtime.test")
        self.assertEqual(loaded["source"], "command_execution")
        self.assertEqual(loaded["workflow_ir"]["steps"][0]["command"], "status")
        self.assertEqual(len(loaded["policy_decisions"]), 1)
        self.assertGreaterEqual(len(loaded["replay_events"]), 3)


if __name__ == "__main__":
    unittest.main()
