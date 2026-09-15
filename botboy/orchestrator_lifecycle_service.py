from __future__ import annotations

from typing import Any, Optional

from botboy.security_context import SecurityContext
from botboy.task_security import TaskSecurityStore
from botboy.tasks import TASK_STATUS_QUEUED, TaskContext
from botboy.tracing import TraceContext, bind_trace_context, reset_trace_context


class OrchestratorLifecycleService:
    """Lifecycle helpers extracted from the BotBoy orchestrator."""

    def __init__(self, bot: Any) -> None:
        self.bot = bot
        self._task_security_store: Optional[TaskSecurityStore] = None

    @property
    def trace_store(self):
        return getattr(self.bot, "trace_store", None)

    @property
    def task_store(self):
        return getattr(self.bot, "task_store", None)

    @property
    def task_security_store(self) -> Optional[TaskSecurityStore]:
        if not self.task_store:
            return None
        if self._task_security_store is None:
            self._task_security_store = TaskSecurityStore(self.task_store)
        return self._task_security_store

    def persist_security_context(self, context: SecurityContext) -> None:
        store = self.task_security_store
        if store:
            store.save(context)

    def load_security_context(self, task_id: str) -> Optional[SecurityContext]:
        store = self.task_security_store
        return store.load(task_id) if store else None

    def start_trace_run(
        self,
        command: str,
        principal: str,
        request_id: str,
        task_id: str = "",
    ) -> tuple[Optional[TraceContext], Optional[str], Any]:
        if not self.trace_store:
            return None, None, None
        ctx = self.trace_store.create_run(
            command=command,
            principal=principal,
            request_id=request_id,
            task_id=task_id,
        )
        root_span = self.trace_store.start_span(
            ctx,
            component="orchestrator",
            event_type="command",
        )
        ctx.root_span_id = root_span
        ctx.current_span_id = root_span
        token = bind_trace_context(ctx)
        return ctx, root_span, token

    def finish_trace_run(
        self,
        trace_ctx: Optional[TraceContext],
        root_span_id: Optional[str],
        token: Any,
        *,
        status: str,
        cmd_type: str,
        summary: str,
        payload_ref: str = "",
    ) -> None:
        if not self.trace_store or not trace_ctx:
            return
        if root_span_id:
            self.trace_store.finish_span(root_span_id, status=status, payload_ref=payload_ref)
        self.trace_store.finish_run(
            trace_ctx.run_id,
            status=status,
            cmd_type=cmd_type,
            summary=summary,
            payload_ref=payload_ref,
        )
        if token is not None:
            reset_trace_context(token)

    def trace_async_label(
        self,
        trace_ctx: Optional[TraceContext],
        parent_span_id: str,
        component: str,
        event_type: str,
    ):
        async def runner(coro):
            if not self.trace_store or not trace_ctx:
                return await coro
            span_id = self.trace_store.start_span(
                trace_ctx,
                component=component,
                event_type=event_type,
                parent_span_id=parent_span_id,
            )
            try:
                result = await coro
                self.trace_store.finish_span(span_id, status="success")
                return result
            except Exception as exc:
                self.trace_store.finish_span(span_id, status="error", payload_ref=str(exc)[:256])
                raise

        return runner

    def create_task_context(
        self,
        command: str,
        *,
        principal: str,
        request_id: str,
        kind: str = "command",
        owner: str = "botboy",
        summary: str = "",
        parent_task_id: str = "",
        root_task_id: str = "",
        scheduler_task_id: str = "",
        payload: Optional[dict] = None,
        task_context: Optional[TaskContext] = None,
    ) -> Optional[TaskContext]:
        if not self.task_store:
            return task_context
        if task_context:
            return task_context
        title = command.strip()[:120] or kind
        return self.task_store.create_task(
            kind=kind,
            owner=owner,
            title=title,
            summary=summary or f"{kind} lifecycle created",
            status=TASK_STATUS_QUEUED,
            principal=principal,
            request_id=request_id,
            parent_task_id=parent_task_id,
            root_task_id=root_task_id,
            scheduler_task_id=scheduler_task_id,
            command=command,
            payload=payload or {"command": command, "kind": kind},
        )

    @staticmethod
    def task_context_from_record(record) -> TaskContext:
        return record.to_context()

    @staticmethod
    def decorate_with_task(result: dict, task_ctx: Optional[TaskContext], status: str) -> dict:
        if not task_ctx:
            return result
        result.setdefault("data", {})
        result["data"]["task"] = {
            "task_id": task_ctx.task_id,
            "root_task_id": task_ctx.root_task_id,
            "parent_task_id": task_ctx.parent_task_id,
            "status": status,
        }
        return result

    def shutdown_resources(self) -> None:
        for attr in ("scheduler", "history", "memory", "trace_store", "task_store"):
            resource = getattr(self.bot, attr, None)
            close = getattr(resource, "close", None)
            stop = getattr(resource, "stop", None)
            if callable(stop):
                stop()
            elif callable(close):
                close()


def create_orchestrator_lifecycle_service(bot: Any) -> OrchestratorLifecycleService:
    return OrchestratorLifecycleService(bot)


def start_trace_run(
    bot: Any,
    command: str,
    principal: str,
    request_id: str,
    task_id: str = "",
) -> tuple[Optional[TraceContext], Optional[str], Any]:
    return create_orchestrator_lifecycle_service(bot).start_trace_run(
        command=command,
        principal=principal,
        request_id=request_id,
        task_id=task_id,
    )


def finish_trace_run(
    bot: Any,
    trace_ctx: Optional[TraceContext],
    root_span_id: Optional[str],
    token: Any,
    *,
    status: str,
    cmd_type: str,
    summary: str,
    payload_ref: str = "",
) -> None:
    create_orchestrator_lifecycle_service(bot).finish_trace_run(
        trace_ctx,
        root_span_id,
        token,
        status=status,
        cmd_type=cmd_type,
        summary=summary,
        payload_ref=payload_ref,
    )


def create_task_context(
    bot: Any,
    command: str,
    *,
    principal: str,
    request_id: str,
    kind: str = "command",
    owner: str = "botboy",
    summary: str = "",
    parent_task_id: str = "",
    root_task_id: str = "",
    scheduler_task_id: str = "",
    payload: Optional[dict] = None,
    task_context: Optional[TaskContext] = None,
) -> Optional[TaskContext]:
    return create_orchestrator_lifecycle_service(bot).create_task_context(
        command,
        principal=principal,
        request_id=request_id,
        kind=kind,
        owner=owner,
        summary=summary,
        parent_task_id=parent_task_id,
        root_task_id=root_task_id,
        scheduler_task_id=scheduler_task_id,
        payload=payload,
        task_context=task_context,
    )
