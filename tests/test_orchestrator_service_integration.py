from __future__ import annotations

import asyncio
import json
import tempfile
import types
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

from botboy.__main__ import BotBoy
from botboy.a2a_pilot import A2APilotAdapter, BoundedA2APilotRegistry
from botboy.bootstrap_service import BootstrapService
from botboy.core.config import BotBoyConfig
from botboy.task_merge_service import TaskMergeService
from botboy.tasks import (
    DELEGATION_STATUS_BLOCKED_ON_CHILD,
    DELEGATION_STATUS_LEASED,
    TASK_STATUS_BLOCKED,
    TASK_STATUS_COMPLETED,
    TASK_STATUS_QUEUED,
    TASK_STATUS_RUNNING,
    TASK_STATUS_WAITING_APPROVAL,
)
from botboy.worker_handoff_service import WorkerHandoffService
from botboy.workers import WorkerRegistry


class _FakePerformanceMonitor:
    def __init__(self, retention: int) -> None:
        self.retention = retention


class _FakeMetricsCollector:
    @classmethod
    def get(cls):
        return cls()


class _FakeInputValidator:
    NORMAL = "normal"

    def __init__(self, mode: str) -> None:
        self.mode = mode


class _FakeResponseCache:
    def __init__(self, max_size: int) -> None:
        self.max_size = max_size


class _FakeSimpleMemoryEngine:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path


class _FakeSkillManager:
    def __init__(self, *, skills_dir: str, auto_discover: bool, validate_security: bool) -> None:
        self.skills_dir = skills_dir
        self.auto_discover = auto_discover
        self.validate_security = validate_security
        self.loaded = False

    def load_all_skills(self) -> int:
        self.loaded = True
        return 1


class _FakeAgentSkillLibrary:
    @classmethod
    def load(cls):
        return cls()


class _FakeTaskHistory:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path


class _FakeTraceStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path


class _FakeScheduler:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.handlers = {}
        self.started = False

    def register_handler(self, name: str, handler) -> None:
        self.handlers[name] = handler

    def start(self) -> None:
        self.started = True


class _FakeReflectionArchive:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path


class _FakeDelegationAdvisor:
    def __init__(self, workers) -> None:
        self.workers = list(workers)


class _FakeRouter:
    pass


class _FakeArchetypes:
    def __init__(self, *, db_path: str) -> None:
        self.db_path = db_path


class _FakeChannelAdapter:
    def __init__(self, bot, router) -> None:
        self.bot = bot
        self.router = router


class _FakeLLM:
    def __init__(self, config) -> None:
        self.config = config


class _FakeLLMIntegration:
    @classmethod
    def from_config(cls, config):
        return _FakeLLM(config)


class _FakeReflectionEngine:
    def __init__(self, *, llm, max_rounds: int, threshold: float, enabled: bool) -> None:
        self.llm = llm
        self.max_rounds = max_rounds
        self.threshold = threshold
        self.enabled = enabled


class _FakePlanningEngine:
    def __init__(self, *, llm, skills, max_rounds: int) -> None:
        self.llm = llm
        self.skills = skills
        self.max_rounds = max_rounds


class _FakeParallelThinkEngine:
    def __init__(self, *, llm, max_steps: int, threshold: float) -> None:
        self.llm = llm
        self.max_steps = max_steps
        self.threshold = threshold


@dataclass
class FakeTaskRecord:
    task_id: str
    payload: dict = field(default_factory=dict)
    result: dict = field(default_factory=dict)
    status: str = TASK_STATUS_COMPLETED
    owner: str = "owner"
    title: str = "Task"
    root_task_id: str = "root"
    request_id: str = "req"
    run_id: str = "run-1"
    summary: str = ""
    parent_task_id: str = ""
    delegated_to_worker: str = ""
    delegation_status: str = ""
    blocked_by_task_id: str = ""
    blocked_kind: str = ""
    blocked_reason: str = ""
    attempt_count: int = 0
    ended: bool = False

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class FakeArtifact:
    category: str
    label: str
    file_path: str
    media_type: str = "application/json"


class FakeTaskStore:
    def __init__(
        self,
        *,
        db_path: str = ":memory:",
        artifact_root: str | Path | None = None,
        records: list[FakeTaskRecord] | None = None,
        children=None,
    ) -> None:
        self.db_path = db_path
        self.artifact_root = Path(artifact_root or (Path.cwd() / ".botboy-runtime" / "orchestrator-service-tests"))
        self.records = {record.task_id: record for record in (records or [])}
        self.children = children or {}
        self.events: list[dict] = []
        self.artifacts: dict[str, list[FakeArtifact]] = {}

    def _now(self) -> str:
        return "2026-04-03T12:00:00"

    def create_task(self, **kwargs) -> FakeTaskRecord:
        record = FakeTaskRecord(
            task_id=kwargs.get("request_id", f"task-{len(self.records) + 1}"),
            payload=dict(kwargs.get("payload", {})),
            status=str(kwargs.get("status", TASK_STATUS_QUEUED)),
            owner=str(kwargs.get("owner", "")),
            title=str(kwargs.get("title", "")),
            root_task_id=str(kwargs.get("root_task_id", kwargs.get("request_id", "root"))),
            request_id=str(kwargs.get("request_id", "")),
            summary=str(kwargs.get("summary", "")),
            parent_task_id=str(kwargs.get("parent_task_id", "")),
            delegated_to_worker=str(kwargs.get("delegated_to_worker", "")),
            delegation_status=str(kwargs.get("delegation_status", "")),
            attempt_count=int(kwargs.get("attempt_count", 0) or 0),
        )
        self.records[record.task_id] = record
        if record.parent_task_id:
            self.children.setdefault(record.parent_task_id, []).append(record)
        return record

    def get_task(self, task_id):
        return self.records.get(task_id)

    def update_task(self, task_id, **updates):
        record = self.records[task_id]
        for key, value in updates.items():
            setattr(record, key, value)
        return record

    def list_children(self, task_id, limit=200):
        return list(self.children.get(task_id, []))[:limit]

    def get_artifacts(self, task_id, limit=20):
        return list(self.artifacts.get(task_id, []))[:limit]

    def write_artifact(self, task_id, *, category, label, filename, content, media_type):
        task_dir = self.artifact_root / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        file_path = task_dir / filename
        file_path.write_text(content, encoding="utf-8")
        artifact = FakeArtifact(
            category=category,
            label=label,
            file_path=str(file_path),
            media_type=media_type,
        )
        self.artifacts.setdefault(task_id, []).append(artifact)
        return artifact

    def add_event(self, task_id, **event):
        self.events.append({"task_id": task_id, **event})
        return self.events[-1]

    def link_artifacts_from_task(self, parent_task_id, child_task_id, *, category_prefix="merged", label_prefix="Merged child artifact", limit=100):
        linked = []
        for artifact in self.get_artifacts(child_task_id, limit=limit):
            linked.append(
                self.write_artifact(
                    parent_task_id,
                    category=f"{category_prefix}_{artifact.category}",
                    label=f"{label_prefix}: {artifact.label}",
                    filename=Path(artifact.file_path).name,
                    content=Path(artifact.file_path).read_text(encoding="utf-8"),
                    media_type=artifact.media_type,
                )
            )
        return linked


def _make_config(tmpdir: Path, *, memory_engine: str = "simple", llm_enabled: bool = True):
    return types.SimpleNamespace(
        performance=types.SimpleNamespace(metrics_retention=17),
        cache=types.SimpleNamespace(enabled=True, max_size=7),
        memory=types.SimpleNamespace(engine=memory_engine),
        skills=types.SimpleNamespace(auto_discover=True, validate_security=False),
        history=types.SimpleNamespace(enabled=True),
        trace=types.SimpleNamespace(enabled=True),
        tasks=types.SimpleNamespace(enabled=True),
        scheduler=types.SimpleNamespace(enabled=True),
        llm=types.SimpleNamespace(
            enabled=llm_enabled,
            backend="mock",
            model="unit-test",
            api_url="http://localhost:11434",
            api_key="",
            timeout=30,
            reflection_enabled=True,
            planning_enabled=True,
            parallel_think_enabled=True,
        ),
        resolve_memory_db_path=lambda: str(tmpdir / "memory.db"),
        resolve_skills_dir=lambda: str(tmpdir / "skills"),
        resolve_history_db_path=lambda: str(tmpdir / "history.db"),
        resolve_trace_db_path=lambda: str(tmpdir / "trace.db"),
        resolve_task_db_path=lambda: str(tmpdir / "tasks.db"),
        resolve_task_artifact_root=lambda: str(tmpdir / "artifacts"),
        resolve_scheduler_db_path=lambda: str(tmpdir / "scheduler.db"),
    )


def _patch_bootstrap_runtime():
    patches = []
    patches.extend(
        [
            patch("botboy.__main__.PerformanceMonitor", _FakePerformanceMonitor),
            patch("botboy.__main__.MetricsCollector", _FakeMetricsCollector),
            patch("botboy.__main__.InputValidator", _FakeInputValidator),
            patch("botboy.__main__.ResponseCache", _FakeResponseCache),
            patch("botboy.__main__.SimpleMemoryEngine", _FakeSimpleMemoryEngine),
            patch("botboy.__main__.SkillManager", _FakeSkillManager),
            patch("botboy.__main__.AgentSkillLibrary", _FakeAgentSkillLibrary),
            patch("botboy.__main__.TaskHistory", _FakeTaskHistory),
            patch("botboy.__main__.TraceStore", _FakeTraceStore),
            patch("botboy.__main__.TaskStore", FakeTaskStore),
            patch("botboy.__main__.BotBoyScheduler", _FakeScheduler),
            patch("botboy.__main__.ReflectionMemoryArchive", _FakeReflectionArchive),
            patch("botboy.__main__.DelegationAdvisor", _FakeDelegationAdvisor),
            patch("botboy.__main__.get_router", return_value=_FakeRouter()),
            patch("botboy.__main__.ArchetypeDatabase", _FakeArchetypes),
            patch("botboy.__main__.ChannelAdapter", _FakeChannelAdapter),
            patch("botboy.llm.integration.LLMIntegration", _FakeLLMIntegration),
            patch("botboy.llm.reflection.ReflectionEngine", _FakeReflectionEngine),
            patch("botboy.planning.PlanningEngine", _FakePlanningEngine),
            patch("botboy.llm.parallel_think.ParallelThinkEngine", _FakeParallelThinkEngine),
            patch("botboy.bootstrap_service.PerformanceMonitor", _FakePerformanceMonitor),
            patch("botboy.bootstrap_service.MetricsCollector", _FakeMetricsCollector),
            patch("botboy.bootstrap_service.InputValidator", _FakeInputValidator),
            patch("botboy.bootstrap_service.ResponseCache", _FakeResponseCache),
            patch("botboy.bootstrap_service.SimpleMemoryEngine", _FakeSimpleMemoryEngine),
            patch("botboy.bootstrap_service.SkillManager", _FakeSkillManager),
            patch("botboy.bootstrap_service.AgentSkillLibrary", _FakeAgentSkillLibrary),
            patch("botboy.bootstrap_service.TaskHistory", _FakeTaskHistory),
            patch("botboy.bootstrap_service.TraceStore", _FakeTraceStore),
            patch("botboy.bootstrap_service.TaskStore", FakeTaskStore),
            patch("botboy.bootstrap_service.BotBoyScheduler", _FakeScheduler),
            patch("botboy.bootstrap_service.ReflectionMemoryArchive", _FakeReflectionArchive),
            patch("botboy.bootstrap_service.DelegationAdvisor", _FakeDelegationAdvisor),
            patch("botboy.bootstrap_service.get_router", return_value=_FakeRouter()),
            patch("botboy.bootstrap_service.ArchetypeDatabase", _FakeArchetypes),
            patch("botboy.bootstrap_service.ChannelAdapter", _FakeChannelAdapter),
            patch("botboy.bootstrap_service.LLMIntegration", _FakeLLMIntegration),
            patch("botboy.bootstrap_service.ReflectionEngine", _FakeReflectionEngine),
            patch("botboy.bootstrap_service.PlanningEngine", _FakePlanningEngine),
            patch("botboy.bootstrap_service.ParallelThinkEngine", _FakeParallelThinkEngine),
        ]
    )
    return patches


def _make_bot(tmpdir: Path, *, memory_engine: str = "simple", llm_enabled: bool = True):
    config = _make_config(tmpdir, memory_engine=memory_engine, llm_enabled=llm_enabled)
    bot = BotBoy(config)
    return bot


def _make_service_bot(tmpdir: Path, *, memory_engine: str = "simple", llm_enabled: bool = True):
    config = _make_config(tmpdir, memory_engine=memory_engine, llm_enabled=llm_enabled)
    return types.SimpleNamespace(
        config=config,
        workers=WorkerRegistry(),
        _run_scheduled_task=lambda scheduled_task: scheduled_task,
        _hybrid_memory=False,
        _initialized=False,
    )


class OrchestratorServiceIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(tempfile.mkdtemp(prefix="botboy-orchestrator-service-"))

    def setUp(self) -> None:
        self.root = self.__class__.root

    def _start_bootstrap_patches(self):
        patches = _patch_bootstrap_runtime()
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def test_bootstrap_service_matches_orchestrator_initialize(self) -> None:
        self._start_bootstrap_patches()
        legacy_bot = _make_bot(self.root)
        service_bot = _make_service_bot(self.root)

        self.assertTrue(legacy_bot.initialize())
        self.assertTrue(BootstrapService(service_bot).initialize())

        self.assertTrue(legacy_bot._initialized)
        self.assertTrue(service_bot._initialized)
        self.assertEqual(legacy_bot.monitor.retention, service_bot.monitor.retention)
        self.assertEqual(type(legacy_bot.metrics), type(service_bot.metrics))
        self.assertEqual(legacy_bot.validator.mode, service_bot.validator.mode)
        self.assertEqual(legacy_bot.cache.max_size, service_bot.cache.max_size)
        self.assertEqual(legacy_bot.memory.db_path, service_bot.memory.db_path)
        self.assertEqual(legacy_bot.skills.skills_dir, service_bot.skills.skills_dir)
        self.assertTrue(legacy_bot.skills.loaded)
        self.assertTrue(service_bot.skills.loaded)
        self.assertIsInstance(legacy_bot.agent_skill_library, _FakeAgentSkillLibrary)
        self.assertIsInstance(service_bot.agent_skill_library, _FakeAgentSkillLibrary)
        self.assertEqual(legacy_bot.history.db_path, service_bot.history.db_path)
        self.assertEqual(legacy_bot.trace_store.db_path, service_bot.trace_store.db_path)
        self.assertEqual(legacy_bot.task_store.artifact_root, service_bot.task_store.artifact_root)
        self.assertEqual(sorted(legacy_bot.scheduler.handlers), sorted(service_bot.scheduler.handlers))
        self.assertTrue(legacy_bot.scheduler.started)
        self.assertTrue(service_bot.scheduler.started)
        self.assertEqual(legacy_bot.reflection_archive.db_path, service_bot.reflection_archive.db_path)
        self.assertEqual(
            [item.adapter_id for item in legacy_bot.a2a_pilot.list_adapters()],
            [item.adapter_id for item in service_bot.a2a_pilot.list_adapters()],
        )
        self.assertEqual(type(legacy_bot.router), type(service_bot.router))
        self.assertEqual(legacy_bot.archetypes.db_path, service_bot.archetypes.db_path)
        self.assertEqual(type(legacy_bot.channel_adapter), type(service_bot.channel_adapter))
        self.assertEqual(type(legacy_bot.llm), type(service_bot.llm))
        self.assertEqual(type(legacy_bot.reflection), type(service_bot.reflection))
        self.assertEqual(type(legacy_bot.planner), type(service_bot.planner))
        self.assertEqual(type(legacy_bot.parallel_thinker), type(service_bot.parallel_thinker))

    def _make_merge_environment(self):
        parent = FakeTaskRecord(
            task_id="parent",
            payload={
                "merge_resolution_policy": "prefer_non_null",
                "merge_resolution_overrides": {"title": "worker:planner"},
            },
            result={
                "merge_policy": "multi_child_sequential_accumulator",
                "child_merges": [
                    {
                        "merged_from_child_task_id": "child-1",
                        "merged_from_worker": "reviewer",
                        "merged_from_owner": "worker:reviewer",
                        "child_status": TASK_STATUS_COMPLETED,
                        "child_summary": "first",
                        "child_result": {"data": {"title": "", "details": "first"}},
                        "linked_artifacts": [{"name": "child-1.json"}],
                    },
                    {
                        "merged_from_child_task_id": "child-2",
                        "merged_from_worker": "planner",
                        "merged_from_owner": "worker:planner",
                        "child_status": TASK_STATUS_COMPLETED,
                        "child_summary": "second",
                        "child_result": {"data": {"title": "final", "details": "first"}},
                        "linked_artifacts": [{"name": "child-2.json"}],
                    },
                ],
            },
        )
        child_active = FakeTaskRecord(task_id="child-active", status=TASK_STATUS_RUNNING)
        store = FakeTaskStore(
            artifact_root=self.root / "merge",
            records=[parent, child_active],
            children={"parent": [child_active]},
        )
        return store, parent

    def test_task_merge_service_matches_orchestrator_merge_contract(self) -> None:
        legacy_store, _ = self._make_merge_environment()
        service_store, _ = self._make_merge_environment()

        legacy_bot = _make_bot(self.root)
        legacy_bot.task_store = legacy_store
        service_bot = types.SimpleNamespace(task_store=service_store)
        service = TaskMergeService(service_bot)

        legacy_payload = BotBoy.get_task_merge_payload(legacy_bot, "parent")
        service_payload = service.get_task_merge_payload("parent")
        self.assertEqual(legacy_payload, service_payload)
        self.assertEqual(BotBoy.get_merge_review_queue(legacy_bot, limit=10), service.get_merge_review_queue(limit=10))
        self.assertEqual(
            BotBoy.get_operator_workbench_payload(legacy_bot, limit=10),
            service.get_operator_workbench_payload(limit=10),
        )

        legacy_updated = BotBoy.set_task_merge_resolution_policy(
            legacy_bot,
            "parent",
            policy="prefer_non_null",
            principal="tester",
            request_id="req-merge",
        )
        service_updated = service.set_task_merge_resolution_policy(
            "parent",
            policy="prefer_non_null",
            principal="tester",
            request_id="req-merge",
        )
        self.assertEqual(
            BotBoy.get_task_merge_payload(legacy_bot, "parent", record=legacy_updated),
            service.get_task_merge_payload("parent", record=service_updated),
        )

    def _make_handoff_environment(self):
        store = FakeTaskStore(
            artifact_root=self.root / "handoff",
            records=[],
        )
        parent = store.create_task(
            title="Parent task",
            kind="command",
            owner="orchestrator",
            principal="tester",
            request_id="req-parent",
            command="parent",
            payload={},
            status=TASK_STATUS_QUEUED,
            summary="parent",
        )
        workers = {worker.worker_id: worker for worker in WorkerRegistry().list_workers()}
        invocations: list[dict] = []

        async def process_command(
            command: str,
            *,
            principal: str,
            request_id: str,
            roles: list[str],
            approval_context: dict,
            task_context,
        ) -> dict:
            invocations.append(
                {
                    "command": command,
                    "principal": principal,
                    "request_id": request_id,
                    "roles": list(roles),
                    "approval_context": dict(approval_context),
                    "task_id": task_context.task_id,
                }
            )
            response = {
                "success": True,
                "data": {"task_status": TASK_STATUS_COMPLETED, "shared": command},
            }
            store.update_task(
                task_context.task_id,
                status=TASK_STATUS_COMPLETED,
                summary=f"{command} complete",
                ended=True,
                result=response,
            )
            return response

        return store, parent, workers, invocations, process_command

    def test_worker_handoff_service_matches_orchestrator_handoff_contract(self) -> None:
        (
            legacy_store,
            legacy_parent,
            legacy_workers,
            legacy_invocations,
            legacy_process_command,
        ) = self._make_handoff_environment()
        (
            service_store,
            service_parent,
            service_workers,
            service_invocations,
            service_process_command,
        ) = self._make_handoff_environment()

        legacy_bot = _make_bot(self.root)
        legacy_bot.task_store = legacy_store
        legacy_bot.workers = legacy_workers
        legacy_bot.process_command = legacy_process_command

        service_bot = types.SimpleNamespace(
            task_store=service_store,
            workers=service_workers,
            process_command=service_process_command,
        )
        service = WorkerHandoffService(service_bot)

        async def run():
            legacy_result = await BotBoy._run_worker_handoff_batch(
                legacy_bot,
                parent_task=legacy_parent,
                specs=[("planner", "planner work"), ("executor", "executor work")],
                resolution_policy="last_child_wins",
                principal="tester",
                roles=["plan", "build"],
                approval_context={"granted": True, "explicit": True},
            )
            service_result = await service._run_worker_handoff_batch(
                parent_task=service_parent,
                specs=[("planner", "planner work"), ("executor", "executor work")],
                resolution_policy="last_child_wins",
                principal="tester",
                roles=["plan", "build"],
                approval_context={"granted": True, "explicit": True},
            )
            return legacy_result, service_result

        legacy_result, service_result = asyncio.run(run())
        self.assertEqual(legacy_result, service_result)
        self.assertEqual(len(legacy_invocations), len(service_invocations))
        self.assertEqual(legacy_bot.task_store.get_task(legacy_parent.task_id).status, TASK_STATUS_COMPLETED)
        self.assertEqual(service_bot.task_store.get_task(service_parent.task_id).status, TASK_STATUS_COMPLETED)
        self.assertEqual(
            legacy_bot.task_store.get_task(legacy_parent.task_id).result["merge_resolution"]["resolved_data"],
            service_bot.task_store.get_task(service_parent.task_id).result["merge_resolution"]["resolved_data"],
        )
        self.assertEqual(
            legacy_bot.task_store.get_task(legacy_parent.task_id).result["merged_from_worker"],
            service_bot.task_store.get_task(service_parent.task_id).result["merged_from_worker"],
        )
        self.assertEqual(
            legacy_bot.task_store.get_task(legacy_parent.task_id).result["merge_policy"],
            service_bot.task_store.get_task(service_parent.task_id).result["merge_policy"],
        )


if __name__ == "__main__":
    unittest.main()
