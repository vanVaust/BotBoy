from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from botboy.orchestrator_lifecycle_service import (
    OrchestratorLifecycleService,
    create_orchestrator_lifecycle_service,
)
from botboy.tasks import TaskContext, TASK_STATUS_QUEUED
from botboy.tracing import TraceContext


class FakeTraceStore:
    def __init__(self) -> None:
        self.calls = []

    def create_run(self, **kwargs):
        self.calls.append(("create_run", kwargs))
        return TraceContext(
            run_id="run-1",
            trace_id="trace-1",
            task_id=kwargs["task_id"],
            principal=kwargs["principal"],
            request_id=kwargs["request_id"],
        )

    def start_span(self, ctx, **kwargs):
        self.calls.append(("start_span", ctx.run_id, kwargs))
        return "span-1"

    def finish_span(self, span_id, **kwargs):
        self.calls.append(("finish_span", span_id, kwargs))

    def finish_run(self, run_id, **kwargs):
        self.calls.append(("finish_run", run_id, kwargs))


class FakeTaskStore:
    def __init__(self) -> None:
        self.calls = []

    def create_task(self, **kwargs):
        self.calls.append(kwargs)
        return TaskContext(
            task_id="task-1",
            root_task_id=kwargs["root_task_id"],
            parent_task_id=kwargs["parent_task_id"],
            kind=kwargs["kind"],
            owner=kwargs["owner"],
            principal=kwargs["principal"],
            request_id=kwargs["request_id"],
            scheduler_task_id=kwargs["scheduler_task_id"],
            command=kwargs["command"],
            status=kwargs["status"],
            run_id="run-1",
        )


class ClosableResource:
    def __init__(self, name: str, calls: list[str]) -> None:
        self.name = name
        self.calls = calls

    def close(self) -> None:
        self.calls.append(f"{self.name}.close")


class StoppableResource(ClosableResource):
    def stop(self) -> None:
        self.calls.append(f"{self.name}.stop")


class FakeBot:
    def __init__(self) -> None:
        self.trace_store = FakeTraceStore()
        self.task_store = FakeTaskStore()
        self.calls: list[str] = []
        self.scheduler = StoppableResource("scheduler", self.calls)
        self.history = ClosableResource("history", self.calls)
        self.memory = ClosableResource("memory", self.calls)
        self.trace_store_for_shutdown = ClosableResource("trace_store", self.calls)
        self.task_store_for_shutdown = ClosableResource("task_store", self.calls)
        self.trace_store.close = self.trace_store_for_shutdown.close  # type: ignore[attr-defined]
        self.task_store.close = self.task_store_for_shutdown.close  # type: ignore[attr-defined]


class OrchestratorLifecycleServiceTests(unittest.TestCase):
    def test_start_and_finish_trace_run(self) -> None:
        bot = FakeBot()
        service = create_orchestrator_lifecycle_service(bot)

        with patch("botboy.orchestrator_lifecycle_service.bind_trace_context", return_value="token") as bind_mock, patch(
            "botboy.orchestrator_lifecycle_service.reset_trace_context"
        ) as reset_mock:
            ctx, root_span_id, token = service.start_trace_run(
                "plan task",
                "alice",
                "req-1",
                task_id="task-9",
            )
            self.assertIsInstance(ctx, TraceContext)
            self.assertEqual(root_span_id, "span-1")
            self.assertEqual(token, "token")
            self.assertEqual(ctx.root_span_id, "span-1")
            self.assertEqual(ctx.current_span_id, "span-1")
            self.assertEqual(
                bot.trace_store.calls[0],
                (
                    "create_run",
                    {
                        "command": "plan task",
                        "principal": "alice",
                        "request_id": "req-1",
                        "task_id": "task-9",
                    },
                ),
            )
            self.assertEqual(bot.trace_store.calls[1][0], "start_span")
            service.finish_trace_run(
                ctx,
                root_span_id,
                token,
                status="success",
                cmd_type="plan",
                summary="done",
                payload_ref="payload-1",
            )
            self.assertEqual(bot.trace_store.calls[2][0], "finish_span")
            self.assertEqual(bot.trace_store.calls[3][0], "finish_run")
            reset_mock.assert_called_once_with("token")
            bind_mock.assert_called_once()

    def test_create_task_context(self) -> None:
        bot = FakeBot()
        service = create_orchestrator_lifecycle_service(bot)

        task_ctx = service.create_task_context(
            "  do something important  ",
            principal="alice",
            request_id="req-2",
            kind="command",
        )

        self.assertIsInstance(task_ctx, TaskContext)
        self.assertEqual(task_ctx.task_id, "task-1")
        self.assertEqual(bot.task_store.calls[0]["title"], "do something important")
        self.assertEqual(bot.task_store.calls[0]["summary"], "command lifecycle created")
        self.assertEqual(
            bot.task_store.calls[0]["payload"],
            {"command": "  do something important  ", "kind": "command"},
        )

    def test_create_task_context_passthrough(self) -> None:
        bot = FakeBot()
        service = create_orchestrator_lifecycle_service(bot)
        existing = TaskContext(
            task_id="task-2",
            root_task_id="task-2",
            parent_task_id="",
            kind="command",
            owner="botboy",
            principal="alice",
            request_id="req-3",
            scheduler_task_id="",
            command="noop",
            status=TASK_STATUS_QUEUED,
            run_id="run-2",
        )
        self.assertIs(service.create_task_context("noop", principal="alice", request_id="req-3", task_context=existing), existing)

    def test_decorate_with_task(self) -> None:
        task_ctx = TaskContext(
            task_id="task-3",
            root_task_id="root-3",
            parent_task_id="parent-3",
            kind="command",
            owner="botboy",
            principal="alice",
            request_id="req-4",
            scheduler_task_id="",
            command="run",
            status=TASK_STATUS_QUEUED,
            run_id="run-3",
        )
        result = {"success": True}
        decorated = OrchestratorLifecycleService.decorate_with_task(result, task_ctx, "running")
        self.assertIs(decorated, result)
        self.assertEqual(
            decorated["data"]["task"],
            {
                "task_id": "task-3",
                "root_task_id": "root-3",
                "parent_task_id": "parent-3",
                "status": "running",
            },
        )

    def test_trace_async_label_wraps_success_and_error(self) -> None:
        bot = FakeBot()
        service = create_orchestrator_lifecycle_service(bot)
        trace_ctx = TraceContext(
            run_id="run-async",
            trace_id="trace-async",
            task_id="task-async",
            principal="alice",
            request_id="req-async",
        )

        async def _success():
            return "ok"

        async def _failure():
            raise RuntimeError("boom")

        runner = service.trace_async_label(trace_ctx, "parent-span", "worker", "step")
        result = asyncio.run(runner(_success()))
        self.assertEqual(result, "ok")
        self.assertEqual(bot.trace_store.calls[-1][0], "finish_span")
        self.assertEqual(bot.trace_store.calls[-1][2]["status"], "success")

        with self.assertRaisesRegex(RuntimeError, "boom"):
            asyncio.run(runner(_failure()))
        self.assertEqual(bot.trace_store.calls[-1][0], "finish_span")
        self.assertEqual(bot.trace_store.calls[-1][2]["status"], "error")

    def test_task_context_from_record(self) -> None:
        task_ctx = OrchestratorLifecycleService.task_context_from_record(
            TaskContext(
                task_id="task-4",
                root_task_id="root-4",
                parent_task_id="parent-4",
                kind="command",
                owner="botboy",
                principal="alice",
                request_id="req-5",
                scheduler_task_id="sched-1",
                command="run",
                status=TASK_STATUS_QUEUED,
                run_id="run-4",
            )
        )
        self.assertEqual(task_ctx.task_id, "task-4")
        self.assertEqual(task_ctx.root_task_id, "root-4")
        self.assertEqual(task_ctx.parent_task_id, "parent-4")

    def test_shutdown_resources(self) -> None:
        bot = FakeBot()
        service = create_orchestrator_lifecycle_service(bot)

        service.shutdown_resources()

        self.assertEqual(
            bot.calls,
            [
                "scheduler.stop",
                "history.close",
                "memory.close",
                "trace_store.close",
                "task_store.close",
            ],
        )


if __name__ == "__main__":
    unittest.main()
