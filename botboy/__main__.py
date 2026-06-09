"""BotBoy v0.6.0-dev ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â Core Orchestrator with full Phase-0 integration."""
from __future__ import annotations

import asyncio
import time
from typing import Any, List, Optional

from botboy.a2a_pilot import BoundedA2APilotRegistry
from botboy.async_command_support import (
    handle_evals as build_evals_response,
    handle_task_resume_command as build_task_resume_response,
)
from botboy.bootstrap_service import BootstrapService
from botboy.command_execution_service import CommandExecutionService
from botboy.command_router import route_command
from botboy.agent_ops_support import (
    handle_a2a as build_a2a_command_response,
    handle_agent_skill_library as build_agent_skill_library_response,
    handle_agent_skill_route as build_agent_skill_route_response,
    handle_contracts as build_contracts_response,
    handle_handoff_suggest as build_handoff_suggest_response,
)
from botboy.agent_skills import (
    AgentSkillLibrary,
)
from botboy.task_merge_helpers import (
    merge_override_source_matches,
    merge_value_is_non_null,
    merge_value_richness_score,
    merge_worker_priority,
    normalize_merge_resolution_policy,
    normalize_merge_review_preset,
)
from botboy.delegation_advisor import DelegationAdvisor
from botboy.dashboard_support import get_dashboard_payload as build_dashboard_payload, load_status_snapshot as load_dashboard_status_snapshot
from botboy.history_support import handle_history as build_history_response
from botboy.introspection_command_support import (
    handle_archetypes as build_archetypes_response,
    handle_reflection_archive as build_reflection_archive_response,
    handle_workers as build_workers_response,
)
from botboy.introspection_support import (
    get_a2a_pilot_payload as build_a2a_pilot_payload,
    get_delegation_intelligence_payload as build_delegation_intelligence_payload,
    get_monitoring_payload as build_monitoring_payload,
    get_reflection_memory_payload as build_reflection_memory_payload,
    get_worker_payload as build_worker_payload,
)
from botboy.orchestrator_lifecycle_service import OrchestratorLifecycleService
from botboy.operator_workbench_support import (
    get_merge_review_queue as build_merge_review_queue,
    get_operator_workbench_payload as build_operator_workbench_payload,
    merge_review_signal_fields as build_merge_review_signal_fields,
    summarize_merge_queue_item as build_merge_queue_item_summary,
)
from botboy.runtime_surface_support import (
    build_help_response as build_help_command_response,
    build_status_response as build_status_command_response,
)
from botboy.core.config import BotBoyConfig
from botboy.task_command_support import handle_task_command as build_task_command_response
from botboy.task_merge_service import TaskMergeService
from botboy.memory.simple import SimpleMemoryEngine
from botboy.skills.manager import SkillManager
from botboy.monitoring import PerformanceMonitor, timed
from botboy.cache import ResponseCache
from botboy.metrics import MetricsCollector
from botboy.runtime import available_modules as runtime_available_modules, get_bot as runtime_get_bot
from botboy.runtime_command_support import handle_schedule as build_schedule_command_response
from botboy.validation import InputValidator
from botboy.history import TaskHistory
from botboy.scheduler import BotBoyScheduler
from botboy.channels import FiveChannelRouter, ChannelAdapter, get_router
from botboy.archetypes import ArchetypeDatabase, BehavioralArchetype
from botboy.tracing import TraceStore, TraceContext
from botboy.tasks import (
    DELEGATION_STATUS_AWAITING_MERGE,
    DELEGATION_STATUS_BLOCKED_ON_CHILD,
    DELEGATION_STATUS_DELEGATED,
    DELEGATION_STATUS_LEASED,
    DELEGATION_STATUS_NONE,
    TASK_STATUS_BLOCKED,
    TASK_STATUS_CANCELLED,
    TASK_STATUS_COMPLETED,
    TASK_STATUS_FAILED,
    TASK_STATUS_QUEUED,
    TASK_STATUS_RUNNING,
    TASK_STATUS_WAITING_APPROVAL,
    TaskContext,
    TaskStore,
)
from botboy.reflection_memory import ReflectionMemoryArchive
from botboy.worker_handoff_service import WorkerHandoffService
from botboy.workers import WorkerRegistry
from botboy.constants import (
    DEFAULT_MERGE_RESOLUTION_POLICY,
    MERGE_RESOLUTION_FIRST_CHILD_WINS,
    MERGE_RESOLUTION_LAST_CHILD_WINS,
    MERGE_RESOLUTION_PREFER_NON_NULL,
    MERGE_RESOLUTION_PREFER_RICHER_VALUE,
    MERGE_RESOLUTION_PREFER_WORKER_PRIORITY,
    MERGE_REVIEW_ACTIONS,
    MERGE_REVIEW_PRESETS,
    MERGE_WORKER_PRIORITY,
    SUPPORTED_MERGE_RESOLUTION_POLICIES,
)

class BotBoy:
    """
    BotBoy Core Orchestrator.

    Initialisation order:
        1. Config (YAML + env overlay)
        2. PerformanceMonitor
        3. MetricsCollector
        4. InputValidator
        5. ResponseCache
        6. SimpleMemoryEngine
        7. SkillManager (SKILL.md auto-discovery)
        8. TaskHistory          [Phase 0 integration]
        9. BotBoyScheduler      [Phase 0 integration]
       10. LLMIntegration       (optional)
       11. ReflectionEngine     (optional, requires LLM)
       12. PlanningEngine       (optional, requires LLM)
       13. ParallelThinkEngine  (optional, requires LLM)

    process_command() flow:
        Cache lookup ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ InputValidator ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ Command router ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢
        TaskHistory.record() ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ MetricsCollector.record_command()
    """

    VERSION = "0.6.0-dev"

    def __init__(self, config: Optional[BotBoyConfig] = None) -> None:
        self.config = config or BotBoyConfig.load()
        self.monitor: Optional[PerformanceMonitor] = None
        self.metrics: Optional[MetricsCollector] = None
        self.validator: Optional[InputValidator] = None
        self.cache: Optional[ResponseCache] = None
        self.memory: Optional[SimpleMemoryEngine] = None
        self.skills: Optional[SkillManager] = None
        self.agent_skill_library: Optional[AgentSkillLibrary] = None
        self.history: Optional[TaskHistory] = None
        self.trace_store: Optional[TraceStore] = None
        self.task_store: Optional[TaskStore] = None
        self.scheduler: Optional[BotBoyScheduler] = None
        self.llm = None
        self.reflection = None
        self.planner = None
        self.parallel_thinker = None
        self.reflection_archive: Optional[ReflectionMemoryArchive] = None

        self.delegation_advisor: Optional[DelegationAdvisor] = None
        self.a2a_pilot: Optional[BoundedA2APilotRegistry] = None
        self._initialized = False
        self.router: Optional[FiveChannelRouter] = None
        self.archetypes: Optional[ArchetypeDatabase] = None
        self.channel_adapter: Optional[ChannelAdapter] = None
        self.workers = WorkerRegistry()
        self.dag_engine = None
        self.self_healing = None
        self.governance = None
        self._hybrid_memory = False
        self._start_time = time.time()

        # Phase 2: Proper Dependency Injection for Services
        self.bootstrap_service = BootstrapService(self)
        self.task_merge_service = TaskMergeService(self)
        self.worker_handoff_service = WorkerHandoffService(self)
        self.lifecycle_service = OrchestratorLifecycleService(self)
        self.command_execution_service = CommandExecutionService(self)

    def _bootstrap_service(self) -> BootstrapService:
        return self.bootstrap_service

    def _task_merge_service(self) -> TaskMergeService:
        return self.task_merge_service

    def _worker_handoff_service(self) -> WorkerHandoffService:
        return self.worker_handoff_service

    def _lifecycle_service(self) -> OrchestratorLifecycleService:
        return self.lifecycle_service

    def _command_execution_service(self) -> CommandExecutionService:
        return self.command_execution_service

    def initialize(self) -> bool:
        """Initialise all components. Returns True on success."""
        return self._bootstrap_service().initialize()

    def _init_llm(self) -> None:
        """Initialise LLM integration and optional AI engines."""
        self._bootstrap_service()._init_llm()

    def _register_default_a2a_adapters(self) -> None:
        self._bootstrap_service()._register_default_a2a_adapters()

    def load_status_snapshot(self) -> dict:
        return load_dashboard_status_snapshot(self)

    def get_monitoring_payload(self) -> dict:
        return build_monitoring_payload(self)

    def get_dashboard_payload(self, mode: str = "local") -> dict:
        return build_dashboard_payload(self, mode=mode)

    def get_worker_payload(self) -> dict:
        return build_worker_payload(self)

    def get_reflection_memory_payload(self) -> dict:
        return build_reflection_memory_payload(self)

    def get_delegation_intelligence_payload(self) -> dict:
        return build_delegation_intelligence_payload(self)

    def get_a2a_pilot_payload(self) -> dict:
        return build_a2a_pilot_payload(self)

    def _start_trace_run(
        self,
        command: str,
        principal: str,
        request_id: str,
        task_id: str = "",
    ) -> tuple[Optional[TraceContext], Optional[str], Any]:
        return self._lifecycle_service().start_trace_run(
            command,
            principal,
            request_id,
            task_id=task_id,
        )

    def _finish_trace_run(
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
        self._lifecycle_service().finish_trace_run(
            trace_ctx,
            root_span_id,
            token,
            status=status,
            cmd_type=cmd_type,
            summary=summary,
            payload_ref=payload_ref,
        )

    def _trace_async_label(self, trace_ctx: Optional[TraceContext], parent_span_id: str, component: str, event_type: str):
        return self._lifecycle_service().trace_async_label(
            trace_ctx,
            parent_span_id,
            component,
            event_type,
        )

    def _create_task_context(
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
        return self._lifecycle_service().create_task_context(
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

    @staticmethod
    def _task_context_from_record(record) -> TaskContext:
        return OrchestratorLifecycleService.task_context_from_record(record)

    def _infer_task_status(self, result: dict) -> str:
        return self._worker_handoff_service()._infer_task_status(result)

    def _decorate_with_task(self, result: dict, task_ctx: Optional[TaskContext], status: str) -> dict:
        return self._lifecycle_service().decorate_with_task(result, task_ctx, status)

    @staticmethod
    def _child_request_id(root_request_id: str, worker_id: str, attempt: int) -> str:
        return WorkerHandoffService._child_request_id(root_request_id, worker_id, attempt)

    @staticmethod
    def _normalize_merge_resolution_policy(policy: str, *, strict: bool = False) -> str:
        return normalize_merge_resolution_policy(
            policy,
            strict=strict,
            default_policy=MERGE_RESOLUTION_LAST_CHILD_WINS,
            supported_policies=SUPPORTED_MERGE_RESOLUTION_POLICIES,
        )

    @staticmethod
    def _merge_value_is_non_null(value: Any) -> bool:
        return merge_value_is_non_null(value)

    @classmethod
    def _merge_value_richness_score(cls, value: Any) -> tuple[int, int]:
        return merge_value_richness_score(value)

    @staticmethod
    def _merge_worker_priority(worker_id: str) -> int:
        return merge_worker_priority(worker_id, MERGE_WORKER_PRIORITY)

    @staticmethod
    def _merge_override_source_matches(source: str, *, task_id: str, worker_id: str) -> bool:
        return merge_override_source_matches(source, task_id=task_id, worker_id=worker_id)

    @classmethod
    def _parse_handoff_batch_request(cls, spec_text: str) -> tuple[str, List[tuple[str, str]]]:
        return WorkerHandoffService._parse_handoff_batch_request(spec_text)

    @staticmethod
    def _parse_handoff_batch_specs(spec_text: str) -> List[tuple[str, str]]:
        return WorkerHandoffService._parse_handoff_batch_specs(spec_text)

    def _merge_resolution_policy_for_record(self, record) -> str:
        return self._task_merge_service().merge_resolution_policy_for_record(record)

    def _merge_resolution_overrides_for_record(self, record) -> dict[str, str]:
        return self._task_merge_service().merge_resolution_overrides_for_record(record)

    @staticmethod
    def _normalize_merge_review_preset(preset: str, *, strict: bool = False) -> str:
        return normalize_merge_review_preset(
            preset,
            strict=strict,
            review_presets=MERGE_REVIEW_PRESETS,
        )

    def _merge_known_review_keys(self, merge: dict) -> list[str]:
        return self._task_merge_service().merge_known_review_keys(merge)

    def _merge_valid_sources(self, merge: dict) -> list[str]:
        return self._task_merge_service().merge_valid_sources(merge)

    def _merge_available_review_presets(self, merge: dict) -> list[dict]:
        return self._task_merge_service().merge_available_review_presets(merge)

    def _merge_review_next_action(
        self,
        *,
        pending_child_ids: list[str],
        review_pending_keys: list[str],
        override_count: int,
    ) -> str:
        return self.task_merge_service.merge_review_next_action(
            pending_child_ids=pending_child_ids,
            review_pending_keys=review_pending_keys,
            override_count=override_count,
        )

    def _merge_review_signal_fields(self, merge: dict) -> dict:
        return self._task_merge_service().merge_review_signal_fields(merge)

    def _summarize_merge_queue_item(self, record, merge: dict) -> dict:
        return self._task_merge_service().summarize_merge_queue_item(record, merge)

    def get_merge_review_queue(
        self,
        *,
        limit: int = 20,
        include_non_actionable: bool = False,
    ) -> dict:
        return self._task_merge_service().get_merge_review_queue(
            limit=limit,
            include_non_actionable=include_non_actionable,
        )

    def get_operator_workbench_payload(self, *, limit: int = 10) -> dict:
        return self._task_merge_service().get_operator_workbench_payload(limit=limit)

    def _merge_child_merges_for_record(self, record) -> list[dict]:
        return self._task_merge_service().merge_child_merges_for_record(record)

    def _build_parent_merge_result(
        self,
        parent_record,
        *,
        child_merges: List[dict],
    ) -> dict:
        return self._task_merge_service().build_parent_merge_result(
            parent_record,
            child_merges=child_merges,
        )

    def _refresh_task_merge_review(
        self,
        task_id: str,
        *,
        principal: str,
        request_id: str,
        event_type: str,
        message: str,
    ):
        return self._task_merge_service().refresh_task_merge_review(
            task_id,
            principal=principal,
            request_id=request_id,
            event_type=event_type,
            message=message,
        )

    def _set_task_merge_resolution_policy(
        self,
        task_id: str,
        *,
        policy: str,
        principal: str,
        request_id: str,
    ):
        return self._task_merge_service()._set_task_merge_resolution_policy(
            task_id,
            policy=policy,
            principal=principal,
            request_id=request_id,
        )

    def set_task_merge_resolution_policy(
        self,
        task_id: str,
        *,
        policy: str,
        principal: str,
        request_id: str,
    ):
        return self._task_merge_service().set_task_merge_resolution_policy(
            task_id,
            policy=policy,
            principal=principal,
            request_id=request_id,
        )

    def set_task_merge_resolution_override(
        self,
        task_id: str,
        *,
        key: str,
        source: str,
        principal: str,
        request_id: str,
    ):
        return self._task_merge_service().set_task_merge_resolution_override(
            task_id,
            key=key,
            source=source,
            principal=principal,
            request_id=request_id,
        )

    def set_task_merge_resolution_overrides_bulk(
        self,
        task_id: str,
        *,
        items: list[dict[str, str]],
        principal: str,
        request_id: str,
    ):
        return self._task_merge_service().set_task_merge_resolution_overrides_bulk(
            task_id,
            items=items,
            principal=principal,
            request_id=request_id,
        )

    def clear_task_merge_resolution_override(
        self,
        task_id: str,
        *,
        key: str,
        principal: str,
        request_id: str,
    ):
        return self._task_merge_service().clear_task_merge_resolution_override(
            task_id,
            key=key,
            principal=principal,
            request_id=request_id,
        )

    def clear_task_merge_resolution_overrides_bulk(
        self,
        task_id: str,
        *,
        keys: list[str],
        principal: str,
        request_id: str,
    ):
        return self._task_merge_service().clear_task_merge_resolution_overrides_bulk(
            task_id,
            keys=keys,
            principal=principal,
            request_id=request_id,
        )

    def resolve_all_task_merge_keys_by_source(
        self,
        task_id: str,
        *,
        source: str,
        keys: Optional[list[str]] = None,
        principal: str,
        request_id: str,
    ):
        return self._task_merge_service().resolve_all_task_merge_keys_by_source(
            task_id,
            source=source,
            keys=keys,
            principal=principal,
            request_id=request_id,
        )

    def apply_task_merge_review_preset(
        self,
        task_id: str,
        *,
        preset: str,
        principal: str,
        request_id: str,
    ):
        return self._task_merge_service().apply_task_merge_review_preset(
            task_id,
            preset=preset,
            principal=principal,
            request_id=request_id,
        )

    def reapply_task_merge_resolution(
        self,
        task_id: str,
        *,
        principal: str,
        request_id: str,
    ):
        return self._task_merge_service().reapply_task_merge_resolution(
            task_id,
            principal=principal,
            request_id=request_id,
        )

    def apply_task_merge_review_action(
        self,
        task_id: str,
        *,
        action: str,
        key: str = "",
        source: str = "",
        keys: Optional[list[str]] = None,
        items: Optional[list[dict[str, str]]] = None,
        preset: str = "",
        principal: str,
        request_id: str,
    ):
        return self._task_merge_service().apply_task_merge_review_action(
            task_id,
            action=action,
            key=key,
            source=source,
            keys=keys,
            items=items,
            preset=preset,
            principal=principal,
            request_id=request_id,
        )

    def _build_child_approval_context(self, approval_context: Optional[dict]) -> dict:
        return self._worker_handoff_service()._build_child_approval_context(approval_context)

    def _create_worker_child_task(
        self,
        *,
        parent_task: TaskContext,
        worker,
        delegated_command: str,
        principal: str,
        child_request_id: str,
        attempt_count: int,
    ) -> TaskContext:
        return self._worker_handoff_service()._create_worker_child_task(
            parent_task=parent_task,
            worker=worker,
            delegated_command=delegated_command,
            principal=principal,
            child_request_id=child_request_id,
            attempt_count=attempt_count,
        )

    async def _execute_worker_child_task(
        self,
        *,
        parent_task: TaskContext,
        child_ctx: TaskContext,
        worker,
        delegated_command: str,
        principal: str,
        roles: Optional[List[str]] = None,
        approval_context: Optional[dict] = None,
    ) -> tuple[dict, Any, str]:
        return await self._worker_handoff_service()._execute_worker_child_task(
            parent_task=parent_task,
            child_ctx=child_ctx,
            worker=worker,
            delegated_command=delegated_command,
            principal=principal,
            roles=roles,
            approval_context=approval_context,
        )

    async def _run_worker_handoff_batch(
        self,
        *,
        parent_task: TaskContext,
        specs: List[tuple[str, str]],
        resolution_policy: str,
        principal: str,
        roles: Optional[List[str]] = None,
        approval_context: Optional[dict] = None,
    ) -> dict:
        return await self._worker_handoff_service()._run_worker_handoff_batch(
            parent_task=parent_task,
            specs=specs,
            resolution_policy=resolution_policy,
            principal=principal,
            roles=roles,
            approval_context=approval_context,
        )

    async def _run_worker_handoff(
        self,
        *,
        parent_task: TaskContext,
        worker_id: str,
        delegated_command: str,
        principal: str,
        roles: Optional[List[str]] = None,
        approval_context: Optional[dict] = None,
    ) -> dict:
        return await self._worker_handoff_service()._run_worker_handoff(
            parent_task=parent_task,
            worker_id=worker_id,
            delegated_command=delegated_command,
            principal=principal,
            roles=roles,
            approval_context=approval_context,
        )

    def _sync_parent_after_child(
        self,
        child_record,
        *,
        principal: str,
        request_id: str,
    ) -> None:
        self._worker_handoff_service()._sync_parent_after_child(
            child_record,
            principal=principal,
            request_id=request_id,
        )

    def _resolve_child_merge_data(
        self,
        child_merges: List[dict],
        *,
        resolution_policy: str = MERGE_RESOLUTION_LAST_CHILD_WINS,
        resolution_overrides: Optional[dict[str, str]] = None,
    ) -> dict:
        return self._task_merge_service().resolve_child_merge_data(
            child_merges,
            resolution_policy=resolution_policy,
            resolution_overrides=resolution_overrides,
        )

    def _empty_task_merge_payload(self) -> dict:
        return self._task_merge_service().empty_task_merge_payload()

    def _load_task_merge_result(self, record) -> dict:
        return self._task_merge_service().load_task_merge_result(record)

    def get_task_merge_payload(self, task_id: str, *, record=None) -> dict:
        return self._task_merge_service().get_task_merge_payload(task_id, record=record)

    async def _run_scheduled_task(self, scheduled_task) -> None:
        payload = dict(getattr(scheduled_task, "payload", {}) or {})
        command = str(payload.get("command", "") or "").strip()
        principal = str(payload.get("principal", "scheduler")).strip() or "scheduler"
        request_id = (
            str(payload.get("request_id", "")).strip()
            or f"req-scheduler-{scheduled_task.task_id}-{int(time.time())}"
        )
        task_ctx = self._create_task_context(
            command or scheduled_task.name,
            principal=principal,
            request_id=request_id,
            kind="scheduled_run",
            owner="scheduler",
            summary=f"Scheduler run for {scheduled_task.name}",
            scheduler_task_id=scheduled_task.task_id,
            payload={
                "command": command,
                "schedule": scheduled_task.schedule,
                "task_type": scheduled_task.task_type,
                "scheduler_task_id": scheduled_task.task_id,
                "payload": payload,
            },
        )
        if not command:
            if self.task_store and task_ctx:
                self.task_store.update_status(
                    task_ctx.task_id,
                    status=TASK_STATUS_FAILED,
                    summary="Scheduled task has no command payload",
                    result={"success": False, "error": "missing_command"},
                    principal=principal,
                    request_id=request_id,
                    event_type="scheduled_run_failed",
                    message="Scheduled task has no command payload",
                )
            return
        await self.process_command(
            command,
            principal=principal,
            request_id=request_id,
            roles=["system"],
            approval_context={
                "granted": True,
                "explicit": False,
                "reason": "scheduler_internal",
                "source": "scheduler",
            },
            task_context=task_ctx,
        )

    async def process_command(
        self,
        raw_command: str,
        *,
        principal: str = "anonymous",
        request_id: str = "",
        roles: Optional[List[str]] = None,
        approval_context: Optional[dict] = None,
        task_context: Optional[TaskContext] = None,
    ) -> dict:
        """
        Central command-processing pipeline with full Phase-0 integration.

        Returns: {"success": bool, "output": str, "type": str, "data": dict}
        """
        # Phase 1: Governance Pre-Execution Block
        if self.governance:
            org_id = getattr(self.config, "org_id", "default_org")
            cmd_hint = raw_command.split(" ")[0] if raw_command else "unknown"
            if not self.governance.enforce_pre_execution(org_id, skill_name=cmd_hint, risk="medium"):
                return {
                    "success": False,
                    "output": f"Governance block: Execution rejected for {cmd_hint} (org: {org_id}).",
                    "type": "error",
                    "data": {"governance": "blocked"}
                }

        result = await self._command_execution_service().process_command(
            raw_command,
            principal=principal,
            request_id=request_id,
            roles=roles,
            approval_context=approval_context,
            task_context=task_context,
        )

        # Phase 1: Self-Healing & DAG Post-Execution
        if self.self_healing:
            try:
                stale_tasks = self.self_healing.scan_for_stale_tasks(timeout_seconds=3600)
                for st in stale_tasks:
                    self.self_healing.execute_auto_retry(st["task_id"])
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"[SELF-HEALING] Error during auto-retry scan: {e}")

        if self.dag_engine:
            task_info = result.get("data", {}).get("task", {})
            root_id = task_info.get("root_task_id") or task_info.get("task_id")
            if root_id:
                try:
                    await self.dag_engine.execute_fringe(root_id)
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).warning(f"[DAG-ENGINE] Error fanning out fringe for {root_id}: {e}")

        return result
    async def _route(
        self,
        command: str,
        *,
        principal: str = "anonymous",
        roles: Optional[List[str]] = None,
        approval_context: Optional[dict] = None,
        request_id: str = "",
    ) -> dict:
        """Route a validated command to the appropriate handler."""
        return await route_command(
            self,
            command,
            principal=principal,
            roles=roles,
            approval_context=approval_context,
            request_id=request_id,
        )
    async def _handle_schedule(self, command: str) -> dict:
        return build_schedule_command_response(self, command)

    def _handle_contracts(self, command: str) -> dict:
        return build_contracts_response(self, command)

    def _handle_agent_skill_library(self, command: str) -> dict:
        return build_agent_skill_library_response(self, command)

    def _handle_agent_skill_route(self, command: str) -> dict:
        return build_agent_skill_route_response(self, command)

    async def _handle_evals(self, command: str) -> dict:
        return await build_evals_response(self, command)

    async def _handle_history(self, command: str) -> dict:
        return build_history_response(self, command)

    async def _handle_tasks(
        self,
        command: str,
        *,
        principal: str = "anonymous",
        roles: Optional[List[str]] = None,
        request_id: str = "",
    ) -> dict:
        delegated = build_task_command_response(
            self,
            command,
            principal=principal,
            roles=roles or [],
            request_id=request_id,
        )
        if delegated is not None:
            return delegated
        return await build_task_resume_response(
            self,
            command,
            principal=principal,
            roles=roles,
            request_id=request_id,
        )

    def _handle_workers(self, command: str) -> dict:
        return build_workers_response(self, command)

    def _handle_reflection_archive(self, command: str, *, principal: str = "anonymous") -> dict:
        return build_reflection_archive_response(self, command, principal=principal)

    def _handle_handoff_suggest(self, command: str) -> dict:
        return build_handoff_suggest_response(self, command)

    def _handle_a2a(self, command: str, *, principal: str = "anonymous") -> dict:
        return build_a2a_command_response(self, command, principal=principal)

    def _show_help(self) -> dict:
        return build_help_command_response(self)

    def _show_status(self) -> dict:
        return build_status_command_response(self)

    async def _handle_archetypes(self, command: str) -> dict:
        return build_archetypes_response(self, command)

    def shutdown(self) -> None:
        """Graceful shutdown of background threads."""
        self._lifecycle_service().shutdown_resources()


from botboy.cli import main as cli_main


def get_bot(config: Optional[BotBoyConfig] = None) -> BotBoy:
    """Return the shared BotBoy singleton."""
    return runtime_get_bot(config)


def available_modules() -> dict:
    """Report which optional modules are importable."""
    return runtime_available_modules()


def main(argv: Optional[list[str]] = None) -> None:
    """Delegate CLI handling to the extracted module."""
    cli_main(argv)


if __name__ == "__main__":
    main()

