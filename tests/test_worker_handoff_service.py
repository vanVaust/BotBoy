from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace

from botboy.tasks import (
    DELEGATION_STATUS_BLOCKED_ON_CHILD,
    DELEGATION_STATUS_DELEGATED,
    TASK_STATUS_BLOCKED,
    TASK_STATUS_COMPLETED,
    TASK_STATUS_FAILED,
    TASK_STATUS_QUEUED,
    TaskStore,
)
from botboy.worker_handoff_service import WorkerHandoffService
from botboy.workers import WorkerRegistry


class WorkerHandoffServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tempdir = (
            Path.cwd()
            / ".botboy-runtime"
            / "test-worker-handoff-service"
            / f"case-{uuid.uuid4().hex[:8]}"
        )
        self.tempdir.mkdir(parents=True, exist_ok=True)
        base = self.tempdir
        self.store = TaskStore(
            db_path=":memory:",
            artifact_root=str(base),
        )
        self.workers = {worker.worker_id: worker for worker in WorkerRegistry().list_workers()}
        self.invocations: list[dict] = []
        self.command_results: dict[str, dict] = {}

        async def process_command(
            command: str,
            *,
            principal: str,
            request_id: str,
            roles: list[str],
            approval_context: dict,
            task_context,
        ) -> dict:
            self.invocations.append(
                {
                    "command": command,
                    "principal": principal,
                    "request_id": request_id,
                    "roles": list(roles),
                    "approval_context": dict(approval_context),
                    "task_id": task_context.task_id,
                }
            )
            response = dict(
                self.command_results.get(
                    command,
                    {
                        "success": True,
                        "data": {"task_status": TASK_STATUS_COMPLETED, "shared": command},
                    },
                )
            )
            data = dict(response.get("data", {}))
            task_status = str(data.get("task_status", TASK_STATUS_COMPLETED) or TASK_STATUS_COMPLETED)
            response["data"] = data
            response["success"] = task_status == TASK_STATUS_COMPLETED
            self.store.update_task(
                task_context.task_id,
                status=task_status,
                summary=f"{command} complete",
                ended=task_status == TASK_STATUS_COMPLETED,
                result=response,
            )
            return response

        self.process_command = process_command
        self.service = WorkerHandoffService(
            SimpleNamespace(
                task_store=self.store,
                workers=self.workers,
                process_command=self.process_command,
            )
        )

    def tearDown(self) -> None:
        self.store.close()
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def _create_parent(self, **payload) -> object:
        return self.store.create_task(
            title="Parent task",
            kind="command",
            owner="orchestrator",
            principal="tester",
            request_id="req-parent",
            command="parent",
            payload=payload or {},
            status=TASK_STATUS_QUEUED,
            summary="parent",
        )

    def _complete_child(self, task_id: str, *, task_status: str = TASK_STATUS_COMPLETED, shared: str = ""):
        return self.store.update_task(
            task_id,
            status=task_status,
            summary=f"{task_status} child",
            result={
                "success": task_status == TASK_STATUS_COMPLETED,
                "data": {"task_status": task_status, "shared": shared or task_id},
            },
            ended=task_status == TASK_STATUS_COMPLETED,
        )

    def test_build_child_approval_context_inherits_scope_and_defaults(self) -> None:
        context = self.service._build_child_approval_context(
            {
                "granted": True,
                "explicit": True,
                "approval_scope": "manual",
                "note": "keep",
            }
        )
        self.assertTrue(context["granted"])
        self.assertEqual(context["approval_scope"], "manual")
        self.assertEqual(context["source"], "worker_handoff")
        self.assertEqual(context["reason"], "child_execution")
        self.assertEqual(context["note"], "keep")

    def test_create_worker_child_task_records_events(self) -> None:
        parent = self._create_parent()
        worker = self.workers["planner"]
        child_ctx = self.service._create_worker_child_task(
            parent_task=parent,
            worker=worker,
            delegated_command="draft plan",
            principal="tester",
            child_request_id="req-parent-wplanner-1",
            attempt_count=1,
        )
        child_record = self.store.get_task(child_ctx.task_id)
        self.assertIsNotNone(child_record)
        self.assertEqual(child_record.parent_task_id, parent.task_id)
        self.assertEqual(child_record.delegated_to_worker, worker.worker_id)
        self.assertEqual(child_record.delegation_status, DELEGATION_STATUS_DELEGATED)
        self.assertEqual(child_record.status, TASK_STATUS_QUEUED)
        self.assertEqual(child_record.payload["worker_id"], worker.worker_id)
        parent_events = [event.event_type for event in self.store.get_events(parent.task_id)]
        child_events = [event.event_type for event in self.store.get_events(child_ctx.task_id)]
        dispatch_events = self.store.list_dispatch_events(child_task_id=child_ctx.task_id)
        self.assertIn("handoff_spawned", parent_events)
        self.assertIn("worker_queued", child_events)
        self.assertEqual([event["dispatch_status"] for event in dispatch_events], ["queued"])

    async def test_execute_worker_child_task_updates_child_and_parent(self) -> None:
        parent = self._create_parent()
        worker = self.workers["executor"]
        child_ctx = self.service._create_worker_child_task(
            parent_task=parent,
            worker=worker,
            delegated_command="implement feature",
            principal="tester",
            child_request_id="req-parent-wexecutor-1",
            attempt_count=1,
        )
        result, child_record, child_status = await self.service._execute_worker_child_task(
            parent_task=parent,
            child_ctx=child_ctx,
            worker=worker,
            delegated_command="implement feature",
            principal="tester",
            roles=["build"],
            approval_context={"granted": True, "explicit": True, "approval_scope": "manual"},
        )
        self.assertTrue(result["success"])
        self.assertEqual(child_status, TASK_STATUS_COMPLETED)
        self.assertIsNotNone(child_record)
        self.assertEqual(self.invocations[0]["approval_context"]["source"], "worker_handoff")
        self.assertEqual(self.invocations[0]["approval_context"]["approval_scope"], "manual")
        parent_record = self.store.get_task(parent.task_id)
        self.assertEqual(parent_record.status, TASK_STATUS_COMPLETED)
        self.assertEqual(parent_record.result["child_merges"][0]["merged_from_child_task_id"], child_ctx.task_id)
        artifact_categories = [artifact.category for artifact in self.store.get_artifacts(parent.task_id)]
        lease_rows = self.store.list_queue_leases(task_id=child_ctx.task_id, include_released=True, include_expired=True)
        dispatch_statuses = [
            event["dispatch_status"] for event in self.store.list_dispatch_events(child_task_id=child_ctx.task_id)
        ]
        child_events = [event.event_type for event in self.store.get_events(child_ctx.task_id)]
        self.assertIn("merge_report", artifact_categories)
        self.assertTrue(any(row["lease_status"] == "released" for row in lease_rows))
        self.assertIn("queue_lease_claimed", child_events)
        self.assertEqual(dispatch_statuses, ["queued", "daemon_started", "completed"])

    async def test_execute_worker_child_task_reports_failure_and_releases_lease(self) -> None:
        async def failing_process_command(*_args, **_kwargs) -> dict:
            raise RuntimeError("worker exploded")

        service = WorkerHandoffService(
            SimpleNamespace(
                task_store=self.store,
                workers=self.workers,
                process_command=failing_process_command,
            )
        )
        parent = self._create_parent()
        worker = self.workers["executor"]
        child_ctx = service._create_worker_child_task(
            parent_task=parent,
            worker=worker,
            delegated_command="explode",
            principal="tester",
            child_request_id="req-parent-wexecutor-2",
            attempt_count=2,
        )

        result, child_record, child_status = await service._execute_worker_child_task(
            parent_task=parent,
            child_ctx=child_ctx,
            worker=worker,
            delegated_command="explode",
            principal="tester",
            roles=["build"],
            approval_context={"granted": True, "explicit": True},
        )

        self.assertFalse(result["success"])
        self.assertEqual(child_status, TASK_STATUS_FAILED)
        self.assertIsNotNone(child_record)
        self.assertEqual(child_record.status, TASK_STATUS_FAILED)
        self.assertEqual(self.store.get_task(parent.task_id).status, TASK_STATUS_BLOCKED)
        lease_rows = self.store.list_queue_leases(task_id=child_ctx.task_id, include_released=True, include_expired=True)
        dispatch_statuses = [
            event["dispatch_status"] for event in self.store.list_dispatch_events(child_task_id=child_ctx.task_id)
        ]
        child_events = [event.event_type for event in self.store.get_events(child_ctx.task_id)]
        self.assertTrue(any(row["lease_status"] == "released" for row in lease_rows))
        self.assertIn("queue_lease_claimed", child_events)
        self.assertEqual(dispatch_statuses, ["queued", "daemon_started", "failed"])

    async def test_run_worker_handoff_returns_success(self) -> None:
        parent = self._create_parent()
        result = await self.service._run_worker_handoff(
            parent_task=parent,
            worker_id="planner",
            delegated_command="write status",
            principal="tester",
            roles=["plan"],
            approval_context={"granted": True, "explicit": True},
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["worker"]["worker_id"], "planner")
        self.assertEqual(result["data"]["task_status"], TASK_STATUS_COMPLETED)
        self.assertEqual(self.store.get_task(parent.task_id).status, TASK_STATUS_COMPLETED)

    async def test_run_worker_handoff_daemon_claims_created_child_only(self) -> None:
        parent = self._create_parent()
        older = self.store.create_task(
            title="Older planner task",
            kind="worker_handoff",
            owner="worker:planner",
            principal="tester",
            request_id="req-older",
            command="do not run",
            delegated_to_worker="planner",
            status=TASK_STATUS_QUEUED,
            summary="older",
        )

        result = await self.service._run_worker_handoff(
            parent_task=parent,
            worker_id="planner",
            delegated_command="write status",
            principal="tester",
            roles=["plan"],
            approval_context={"granted": True, "explicit": True},
        )

        child_task_id = result["data"]["child_task"]["task_id"]
        self.assertTrue(result["success"])
        self.assertEqual(self.invocations[0]["command"], "write status")
        self.assertEqual(self.invocations[0]["task_id"], child_task_id)
        self.assertEqual(self.store.get_task(older.task_id).status, TASK_STATUS_QUEUED)
        child_events = [event.event_type for event in self.store.get_events(child_task_id)]
        self.assertIn("queue_lease_claimed", child_events)

    async def test_run_worker_handoff_batch_merges_children(self) -> None:
        parent = self._create_parent(payload={"merge_resolution_policy": "prefer_non_null"})
        result = await self.service._run_worker_handoff_batch(
            parent_task=parent,
            specs=[("planner", "planner work"), ("executor", "executor work")],
            resolution_policy="last_child_wins",
            principal="tester",
            roles=["plan", "build"],
            approval_context={"granted": True, "explicit": True},
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["worker_count"], 2)
        self.assertEqual(len(result["data"]["child_results"]), 2)
        parent_record = self.store.get_task(parent.task_id)
        self.assertEqual(parent_record.status, TASK_STATUS_COMPLETED)
        self.assertEqual(parent_record.result["merge_resolution"]["resolved_data"]["shared"], "executor work")
        self.assertEqual(parent_record.result["merged_from_worker"], "executor")
        parent_events = [event.event_type for event in self.store.get_events(parent.task_id)]
        self.assertIn("merge_deferred", parent_events)
        self.assertIn("merge_completed", parent_events)

    def test_sync_parent_after_child_transitions_blocked_then_completed(self) -> None:
        parent = self._create_parent()
        worker = self.workers["reviewer"]
        first_child = self.service._create_worker_child_task(
            parent_task=parent,
            worker=worker,
            delegated_command="review first",
            principal="tester",
            child_request_id="req-parent-wreviewer-1",
            attempt_count=1,
        )
        second_child = self.service._create_worker_child_task(
            parent_task=parent,
            worker=worker,
            delegated_command="review second",
            principal="tester",
            child_request_id="req-parent-wreviewer-2",
            attempt_count=2,
        )
        first_record = self._complete_child(first_child.task_id, shared="first")
        self.service._sync_parent_after_child(first_record, principal="tester", request_id="req-parent")
        parent_record = self.store.get_task(parent.task_id)
        self.assertEqual(parent_record.status, TASK_STATUS_BLOCKED)
        self.assertEqual(parent_record.delegation_status, DELEGATION_STATUS_BLOCKED_ON_CHILD)
        self.assertEqual(parent_record.result["merge_resolution"]["resolved_data"]["shared"], "first")
        second_record = self._complete_child(second_child.task_id, shared="second")
        self.service._sync_parent_after_child(second_record, principal="tester", request_id="req-parent")
        parent_record = self.store.get_task(parent.task_id)
        self.assertEqual(parent_record.status, TASK_STATUS_COMPLETED)
        self.assertEqual(parent_record.result["child_merges"][-1]["merged_from_child_task_id"], second_child.task_id)

    def test_parse_handoff_batch_request_helper(self) -> None:
        policy, specs = self.service._parse_handoff_batch_request("--resolution-policy=prefer_non_null planner:plan")
        self.assertEqual(policy, "prefer_non_null")
        self.assertEqual(specs, [("planner", "plan")])


if __name__ == "__main__":
    unittest.main()
