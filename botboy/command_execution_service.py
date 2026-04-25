from __future__ import annotations

import json
import sqlite3
import time
from typing import Optional

from botboy.tasks import TASK_STATUS_COMPLETED, TASK_STATUS_FAILED, TaskContext
from botboy.workflow_ir import (
    WorkflowReplayRecorder,
    build_policy_decision,
    compile_workflow_ir,
    workflow_step_from_spec,
)
from botboy.workflow_ir_persistence import persist_workflow_ir_snapshot

OPTIONAL_ROUTE_ERRORS = (
    RuntimeError,
    ValueError,
    OSError,
    LookupError,
    ImportError,
    json.JSONDecodeError,
)
BEST_EFFORT_STORE_ERRORS = (
    OSError,
    RuntimeError,
    sqlite3.Error,
)
BEST_EFFORT_ARTIFACT_ERRORS = (
    OSError,
    RuntimeError,
    sqlite3.Error,
    TypeError,
    ValueError,
    json.JSONDecodeError,
)
OPTIONAL_REFLECTION_ERRORS = OPTIONAL_ROUTE_ERRORS + (sqlite3.Error,)
BEST_EFFORT_WORKFLOW_ERRORS = (
    OSError,
    RuntimeError,
    sqlite3.Error,
    TypeError,
    ValueError,
    json.JSONDecodeError,
)


class CommandExecutionService:
    def __init__(self, bot) -> None:
        self.bot = bot

    @staticmethod
    def _default_approval_context() -> dict:
        return {
            "granted": True,
            "explicit": False,
            "reason": "local_default",
            "source": "local",
        }

    def _cache_allowed(self, raw_command: str) -> bool:
        normalized_command = raw_command.strip().lower()
        if normalized_command.startswith(("task ", "worker ", "handoff ")):
            return False
        if self.bot.skills:
            approval_contract = self.bot.skills.get_contract_for_command(raw_command.strip())
            if approval_contract and approval_contract.get("needs_approval"):
                return False
        return True

    def _mark_task_running(
        self,
        task_ctx: Optional[TaskContext],
        *,
        raw_command: str,
        principal_id: str,
        request_id: str,
        trace_ctx,
    ) -> None:
        if not self.bot.task_store or not task_ctx:
            return
        if trace_ctx:
            self.bot.task_store.attach_run(task_ctx.task_id, trace_ctx.run_id)
        self.bot.task_store.mark_running(
            task_ctx.task_id,
            run_id=trace_ctx.run_id if trace_ctx else "",
            message=raw_command[:256],
            principal=principal_id,
            request_id=request_id,
        )

    def _record_cache_hit(
        self,
        cached: dict,
        *,
        task_ctx: Optional[TaskContext],
        trace_ctx,
        root_span_id: str,
        trace_token,
        principal_id: str,
        request_id: str,
    ) -> dict:
        if self.bot.metrics:
            self.bot.metrics.record_cache(hit=True)
        if trace_ctx:
            cached_result = dict(cached)
            cached_data = dict(cached_result.get("data", {}))
            cached_data["trace"] = {"run_id": trace_ctx.run_id, "trace_id": trace_ctx.trace_id}
            cached_result["data"] = cached_data
        else:
            cached_result = cached
        cached_result = self.bot._decorate_with_task(cached_result, task_ctx, TASK_STATUS_COMPLETED)
        if self.bot.task_store and task_ctx:
            self.bot.task_store.update_status(
                task_ctx.task_id,
                status=TASK_STATUS_COMPLETED,
                summary="cache_hit",
                result=cached_result,
                run_id=trace_ctx.run_id if trace_ctx else "",
                principal=principal_id,
                request_id=request_id,
                event_type="cache_hit",
                message="Returned cached command result",
                payload_ref="cache",
            )
        if trace_ctx:
            self.bot._finish_trace_run(
                trace_ctx,
                root_span_id,
                trace_token,
                status="success",
                cmd_type=cached.get("type", "unknown"),
                summary="cache_hit",
                payload_ref="cache",
            )
        return cached_result

    def _validation_failure(
        self,
        output: str,
        *,
        task_ctx: Optional[TaskContext],
        trace_ctx,
        root_span_id: str,
        trace_token,
        principal_id: str,
        request_id: str,
    ) -> dict:
        result = {"success": False, "output": output, "type": "error"}
        result = self.bot._decorate_with_task(result, task_ctx, TASK_STATUS_FAILED)
        if self.bot.task_store and task_ctx:
            self.bot.task_store.update_status(
                task_ctx.task_id,
                status=TASK_STATUS_FAILED,
                summary=result["output"][:256],
                result=result,
                run_id=trace_ctx.run_id if trace_ctx else "",
                principal=principal_id,
                request_id=request_id,
                event_type="validation_failed",
                message=result["output"][:256],
                payload_ref="validation",
            )
        if trace_ctx:
            self.bot._finish_trace_run(
                trace_ctx,
                root_span_id,
                trace_token,
                status="error",
                cmd_type="error",
                summary=result["output"][:256],
                payload_ref="validation",
            )
        return result

    async def _run_route(
        self,
        command: str,
        *,
        principal_id: str,
        roles: list[str],
        approval_context: dict,
        request_id: str,
        task_ctx: Optional[TaskContext],
        trace_ctx,
        root_span_id: str,
        trace_token,
    ) -> dict:
        if self.bot.trace_store and trace_ctx:
            route_span_id = self.bot.trace_store.start_span(
                trace_ctx,
                component="router",
                event_type="route",
                parent_span_id=root_span_id or "",
                payload_ref=command[:256],
            )
        else:
            route_span_id = None

        try:
            result = await self.bot._route(
                command,
                principal=principal_id,
                roles=roles,
                approval_context=approval_context,
                request_id=request_id,
            )
            if route_span_id and self.bot.trace_store:
                self.bot.trace_store.finish_span(route_span_id, status="success")
            return result
        except Exception as exc:
            if self.bot.task_store and task_ctx:
                self.bot.task_store.update_status(
                    task_ctx.task_id,
                    status=TASK_STATUS_FAILED,
                    summary=str(exc)[:256],
                    result={"success": False, "error": str(exc)},
                    run_id=trace_ctx.run_id if trace_ctx else "",
                    principal=principal_id,
                    request_id=request_id,
                    event_type="route_exception",
                    message=str(exc)[:256],
                    payload_ref="route",
                )
            if route_span_id and self.bot.trace_store:
                self.bot.trace_store.finish_span(route_span_id, status="error", payload_ref=str(exc)[:256])
            if trace_ctx:
                self.bot._finish_trace_run(
                    trace_ctx,
                    root_span_id,
                    trace_token,
                    status="error",
                    cmd_type="error",
                    summary=str(exc)[:256],
                    payload_ref="route",
                )
            raise

    def _record_archetype_outcome(self, command: str, result: dict) -> None:
        if not self.bot.archetypes:
            return
        try:
            match = self.bot.archetypes.match_intent(command)
            self.bot.archetypes.record_outcome(
                command=command,
                archetype=match.archetype,
                confidence=match.confidence,
                matched_via=match.matched_via,
                success=result.get("success", False),
            )
        except BEST_EFFORT_STORE_ERRORS:
            pass

    async def _apply_reflection(self, command: str, result: dict, trace_ctx, root_span_id: str) -> dict:
        if not (self.bot.reflection and self.bot.llm and result.get("success") and result.get("type") not in ("error", "llm")):
            return result
        reflection_span = None
        try:
            if self.bot.trace_store and trace_ctx:
                reflection_span = self.bot.trace_store.start_span(
                    trace_ctx,
                    component="reflection",
                    event_type="llm_reflection",
                    parent_span_id=root_span_id or "",
                )
            reflection = await self.bot.reflection.reflect(command, result.get("output", ""))
            if reflection.refined:
                result["output"] = reflection.final
                result["_reflected"] = True
            if reflection_span and self.bot.trace_store:
                self.bot.trace_store.finish_span(reflection_span, status="success")
        except OPTIONAL_REFLECTION_ERRORS:
            if self.bot.trace_store and trace_ctx and reflection_span:
                self.bot.trace_store.finish_span(reflection_span, status="error", payload_ref="reflection_failed")
        return result

    def _record_history_and_metrics(
        self,
        *,
        command: str,
        result: dict,
        latency_ms: float,
        principal_id: str,
        request_id: str,
        task_ctx: Optional[TaskContext],
    ) -> None:
        cmd_type = result.get("type", "unknown")
        if self.bot.monitor:
            self.bot.monitor.record("command.total", latency_ms, principal=principal_id, request_id=request_id)
            self.bot.monitor.record(f"command.{cmd_type}", latency_ms, principal=principal_id, request_id=request_id)
        if self.bot.history:
            try:
                self.bot.history.record(
                    command=command,
                    output=result.get("output", "")[:512],
                    success=result.get("success", False),
                    cmd_type=cmd_type,
                    latency_ms=latency_ms,
                    principal=principal_id,
                    request_id=request_id,
                    task_id=task_ctx.task_id if task_ctx else "",
                )
            except BEST_EFFORT_STORE_ERRORS:
                pass
        if self.bot.metrics:
            self.bot.metrics.record_command(
                cmd_type=cmd_type,
                success=result.get("success", False),
                duration_s=latency_ms / 1000,
                principal=principal_id,
                request_id=request_id,
            )

    def _write_eval_artifact(self, result: dict, *, task_ctx: Optional[TaskContext]) -> None:
        if not (self.bot.task_store and task_ctx and result.get("type") == "evals"):
            return
        try:
            self.bot.task_store.write_artifact(
                task_ctx.task_id,
                category="eval_report",
                label="Eval report",
                filename="eval_report.json",
                content=json.dumps(result.get("data", {}), indent=2, sort_keys=True) + "\n",
                media_type="application/json",
            )
        except BEST_EFFORT_ARTIFACT_ERRORS:
            pass

    def _persist_live_workflow_ir(
        self,
        *,
        command: str,
        result: dict,
        task_ctx: Optional[TaskContext],
        task_status: str,
        principal_id: str,
        request_id: str,
        roles: list[str],
        approval_context: dict,
    ) -> None:
        if not self.bot.task_store:
            return
        try:
            workflow_id = f"wf-live-{task_ctx.task_id}" if task_ctx and task_ctx.task_id else ""
            workflow = compile_workflow_ir(
                goal=command,
                step_specs=[
                    {
                        "step_id": "s1",
                        "command": command,
                        "request_id": request_id,
                        "approval_context": dict(approval_context),
                        "metadata": {"result_type": str(result.get("type", ""))},
                    }
                ],
                principal=principal_id,
                request_id=request_id,
                workflow_id=workflow_id,
                metadata={
                    "runtime_hook": "command_execution_service",
                    "task_id": task_ctx.task_id if task_ctx else "",
                    "task_status": task_status,
                },
            )
            step = workflow_step_from_spec(
                {
                    "step_id": workflow.steps[0].step_id if workflow.steps else "s1",
                    "command": workflow.steps[0].command if workflow.steps else command,
                    "request_id": request_id,
                    "approval_context": dict(approval_context),
                },
                index=0,
                default_request_id=request_id,
            )
            decision = build_policy_decision(
                workflow_id=workflow.workflow_id,
                step=step,
                principal=principal_id,
                roles=roles,
                approval_context=approval_context,
                surface="live_runtime_command",
            )
            workflow.policy_decisions = [decision]
            replay = WorkflowReplayRecorder(workflow.workflow_id)
            replay.record(
                "workflow_started",
                status="running",
                payload={
                    "task_id": task_ctx.task_id if task_ctx else "",
                    "command": command,
                },
            )
            replay.record(
                "step_completed" if result.get("success", False) else "step_failed",
                step_id=step.step_id,
                status=task_status,
                payload={
                    "result_success": bool(result.get("success", False)),
                    "result_type": str(result.get("type", "")),
                },
            )
            replay.record(
                "workflow_completed",
                status=task_status,
                payload={"task_status": task_status},
            )
            persist_workflow_ir_snapshot(
                self.bot.task_store,
                workflow_ir=workflow,
                policy_decisions=[decision],
                replay_events=replay.events,
                task_id=task_ctx.task_id if task_ctx else "",
                request_id=request_id,
                principal=principal_id,
                source="command_execution",
                status=task_status,
            )
        except BEST_EFFORT_WORKFLOW_ERRORS:
            pass

    async def process_command(
        self,
        raw_command: str,
        *,
        principal: str = "anonymous",
        request_id: str = "",
        roles: Optional[list[str]] = None,
        approval_context: Optional[dict] = None,
        task_context: Optional[TaskContext] = None,
    ) -> dict:
        if not raw_command or not raw_command.strip():
            return {"success": False, "output": "Empty command.", "type": "error"}

        start_time = time.perf_counter()
        stripped_command = raw_command.strip()
        principal_id = principal or (task_context.principal if task_context else "") or "anonymous"
        trace_request_id = request_id or (task_context.request_id if task_context else "") or ""
        effective_approval_context = dict(approval_context or {})
        if approval_context is None:
            effective_approval_context = self._default_approval_context()
        roles = roles or []
        cache_allowed = self._cache_allowed(stripped_command)

        task_ctx = self.bot._create_task_context(
            stripped_command,
            principal=principal_id,
            request_id=trace_request_id,
            task_context=task_context,
        )
        trace_ctx, root_span_id, trace_token = self.bot._start_trace_run(
            stripped_command,
            principal_id,
            trace_request_id,
            task_ctx.task_id if task_ctx else "",
        )
        self._mark_task_running(
            task_ctx,
            raw_command=stripped_command,
            principal_id=principal_id,
            request_id=trace_request_id,
            trace_ctx=trace_ctx,
        )

        if self.bot.cache and cache_allowed:
            cached = self.bot.cache.get(raw_command)
            if cached is not None:
                return self._record_cache_hit(
                    cached,
                    task_ctx=task_ctx,
                    trace_ctx=trace_ctx,
                    root_span_id=root_span_id,
                    trace_token=trace_token,
                    principal_id=principal_id,
                    request_id=trace_request_id,
                )
            if self.bot.metrics:
                self.bot.metrics.record_cache(miss=True)

        if self.bot.validator:
            validation = self.bot.validator.validate_command(raw_command)
            if not validation.valid:
                return self._validation_failure(
                    f"Invalid input: {'; '.join(validation.errors)}",
                    task_ctx=task_ctx,
                    trace_ctx=trace_ctx,
                    root_span_id=root_span_id,
                    trace_token=trace_token,
                    principal_id=principal_id,
                    request_id=trace_request_id,
                )
            command = validation.sanitized
        else:
            command = stripped_command

        result = await self._run_route(
            command,
            principal_id=principal_id,
            roles=roles,
            approval_context=effective_approval_context,
            request_id=trace_request_id,
            task_ctx=task_ctx,
            trace_ctx=trace_ctx,
            root_span_id=root_span_id,
            trace_token=trace_token,
        )

        self._record_archetype_outcome(command, result)
        result = await self._apply_reflection(command, result, trace_ctx, root_span_id)

        latency_ms = (time.perf_counter() - start_time) * 1000
        cmd_type = result.get("type", "unknown")
        task_status = self.bot._infer_task_status(result)

        if self.bot.cache and cache_allowed:
            self.bot.cache.set(raw_command, result)

        self._record_history_and_metrics(
            command=command,
            result=result,
            latency_ms=latency_ms,
            principal_id=principal_id,
            request_id=trace_request_id,
            task_ctx=task_ctx,
        )

        if trace_ctx:
            result.setdefault("data", {})
            result["data"].setdefault("trace", {"run_id": trace_ctx.run_id, "trace_id": trace_ctx.trace_id})
        result = self.bot._decorate_with_task(result, task_ctx, task_status)
        self._write_eval_artifact(result, task_ctx=task_ctx)
        self._persist_live_workflow_ir(
            command=command,
            result=result,
            task_ctx=task_ctx,
            task_status=task_status,
            principal_id=principal_id,
            request_id=trace_request_id,
            roles=roles,
            approval_context=effective_approval_context,
        )

        if self.bot.task_store and task_ctx:
            self.bot.task_store.update_status(
                task_ctx.task_id,
                status=task_status,
                summary=result.get("output", "")[:256],
                result=result,
                run_id=trace_ctx.run_id if trace_ctx else "",
                principal=principal_id,
                request_id=trace_request_id,
                event_type="command_result",
                message=result.get("output", "")[:256],
                payload_ref=result.get("type", "unknown"),
            )
        if trace_ctx:
            self.bot._finish_trace_run(
                trace_ctx,
                root_span_id,
                trace_token,
                status="success" if result.get("success", False) else "error",
                cmd_type=cmd_type,
                summary=result.get("output", "")[:256],
                payload_ref=result.get("type", "unknown"),
            )
        return result


async def process_command(
    bot,
    raw_command: str,
    *,
    principal: str = "anonymous",
    request_id: str = "",
    roles: Optional[list[str]] = None,
    approval_context: Optional[dict] = None,
    task_context: Optional[TaskContext] = None,
) -> dict:
    return await CommandExecutionService(bot).process_command(
        raw_command,
        principal=principal,
        request_id=request_id,
        roles=roles,
        approval_context=approval_context,
        task_context=task_context,
    )
