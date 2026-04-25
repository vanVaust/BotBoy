from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from botboy.a2a_pilot import A2APilotAdapter, BoundedA2APilotRegistry
from botboy.bootstrap_service import BootstrapService, bootstrap_bot


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


class _FakeAgentSkillEntry:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeAgentSkillLibrary:
    def __init__(self) -> None:
        self.entry = _FakeAgentSkillEntry("bootstrap")

    @classmethod
    def load(cls):
        return cls()


class _FakeTaskHistory:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path


class _FakeTraceStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path


class _FakeTaskStore:
    def __init__(self, *, db_path: str, artifact_root: str) -> None:
        self.db_path = db_path
        self.artifact_root = artifact_root


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


def _make_bot(tmpdir: Path, *, memory_engine: str = "simple", llm_enabled: bool = True):
    config = _make_config(tmpdir, memory_engine=memory_engine, llm_enabled=llm_enabled)
    bot = types.SimpleNamespace(
        config=config,
        workers=types.SimpleNamespace(list_workers=lambda: [{"worker_id": "reviewer"}]),
        _run_scheduled_task=lambda scheduled_task: scheduled_task,
        _hybrid_memory=False,
        _initialized=False,
    )
    return bot


class BootstrapServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(tempfile.mkdtemp(prefix="botboy-bootstrap-"))

    def setUp(self) -> None:
        self.root = self.__class__.root

    def _patch_runtime(self, *, agent_skill_library_load=None):
        patches = [
            patch("botboy.bootstrap_service.PerformanceMonitor", _FakePerformanceMonitor),
            patch("botboy.bootstrap_service.MetricsCollector", _FakeMetricsCollector),
            patch("botboy.bootstrap_service.InputValidator", _FakeInputValidator),
            patch("botboy.bootstrap_service.ResponseCache", _FakeResponseCache),
            patch("botboy.bootstrap_service.SimpleMemoryEngine", _FakeSimpleMemoryEngine),
            patch("botboy.bootstrap_service.SkillManager", _FakeSkillManager),
            patch("botboy.bootstrap_service.AgentSkillLibrary", _FakeAgentSkillLibrary),
            patch("botboy.bootstrap_service.TaskHistory", _FakeTaskHistory),
            patch("botboy.bootstrap_service.TraceStore", _FakeTraceStore),
            patch("botboy.bootstrap_service.TaskStore", _FakeTaskStore),
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
        if agent_skill_library_load is not None:
            patches.append(
                patch(
                    "botboy.bootstrap_service.AgentSkillLibrary.load",
                    side_effect=agent_skill_library_load,
                )
            )
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def test_initialize_wires_core_components(self) -> None:
        self._patch_runtime()
        bot = _make_bot(self.root)

        service = BootstrapService(bot)
        self.assertTrue(service.initialize())

        self.assertTrue(bot._initialized)
        self.assertEqual(bot.monitor.retention, 17)
        self.assertEqual(bot.metrics.__class__, _FakeMetricsCollector)
        self.assertEqual(bot.validator.mode, _FakeInputValidator.NORMAL)
        self.assertEqual(bot.cache.max_size, 7)
        self.assertEqual(bot.memory.db_path, str(self.root / "memory.db"))
        self.assertTrue(bot.skills.loaded)
        self.assertEqual(bot.skills.skills_dir, str(self.root / "skills"))
        self.assertIsInstance(bot.agent_skill_library, _FakeAgentSkillLibrary)
        self.assertEqual(bot.history.db_path, str(self.root / "history.db"))
        self.assertEqual(bot.trace_store.db_path, str(self.root / "trace.db"))
        self.assertEqual(bot.task_store.db_path, str(self.root / "tasks.db"))
        self.assertEqual(bot.task_store.artifact_root, str(self.root / "artifacts"))
        self.assertTrue(bot.scheduler.started)
        self.assertEqual(sorted(bot.scheduler.handlers), ["command", "generic"])
        self.assertEqual(bot.reflection_archive.db_path, str(self.root / "reflection_memory.db"))
        self.assertEqual(bot.delegation_advisor.workers, [{"worker_id": "reviewer"}])
        self.assertIsInstance(bot.a2a_pilot, BoundedA2APilotRegistry)
        self.assertEqual([adapter.adapter_id for adapter in bot.a2a_pilot.list_adapters()], [
            "executor-local",
            "planner-local",
            "review-local",
        ])
        self.assertIsInstance(bot.router, _FakeRouter)
        self.assertEqual(bot.archetypes.db_path, ":memory:")
        self.assertEqual(bot.channel_adapter.bot, bot)
        self.assertEqual(bot.channel_adapter.router, bot.router)
        self.assertEqual(bot.llm.config.backend, "mock")
        self.assertEqual(bot.reflection.llm, bot.llm)
        self.assertEqual(bot.planner.skills, bot.skills)
        self.assertEqual(bot.parallel_thinker.max_steps, 4)

    def test_initialize_falls_back_when_agent_skill_library_is_missing(self) -> None:
        self._patch_runtime(agent_skill_library_load=FileNotFoundError("missing"))
        bot = _make_bot(self.root)

        service = BootstrapService(bot)
        self.assertTrue(service.initialize())
        self.assertIsNone(bot.agent_skill_library)

    def test_init_llm_installs_optional_engines(self) -> None:
        self._patch_runtime()
        bot = _make_bot(self.root, llm_enabled=True)
        bot.skills = object()
        service = BootstrapService(bot)

        service._init_llm()

        self.assertIsInstance(bot.llm, _FakeLLM)
        self.assertIsInstance(bot.reflection, _FakeReflectionEngine)
        self.assertIsInstance(bot.planner, _FakePlanningEngine)
        self.assertIsInstance(bot.parallel_thinker, _FakeParallelThinkEngine)
        self.assertIs(bot.reflection.llm, bot.llm)
        self.assertIs(bot.planner.skills, bot.skills)

    def test_register_default_a2a_adapters_skips_existing_entries(self) -> None:
        self._patch_runtime()
        bot = _make_bot(self.root)
        bot.a2a_pilot = BoundedA2APilotRegistry(max_payload_bytes=4096)

        existing = bot.a2a_pilot.register(
            A2APilotAdapter(
                adapter_id="planner-local",
                name="Planner Local Adapter",
                handler=lambda message: {"status": "existing"},
                capabilities=["planning"],
            )
        )

        service = BootstrapService(bot)
        service._register_default_a2a_adapters()

        adapters = bot.a2a_pilot.list_adapters()
        self.assertEqual(len(adapters), 3)
        self.assertIs(bot.a2a_pilot.get("planner-local"), existing)

        response = bot.a2a_pilot.dispatch(
            "review-local",
            {"task": "verify"},
            sender="tester",
            principal="tester",
            task_id="task-1",
        )
        payload = response.to_dict()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["payload"]["adapter_id"], "review-local")
        self.assertEqual(payload["payload"]["task_id"], "task-1")
        self.assertIn("merge_review", payload["metadata"]["capabilities"])

    def test_bootstrap_bot_wrapper(self) -> None:
        self._patch_runtime()
        bot = _make_bot(self.root)

        self.assertTrue(bootstrap_bot(bot))


if __name__ == "__main__":
    unittest.main()
