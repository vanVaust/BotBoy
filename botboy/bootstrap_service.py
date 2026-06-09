"""Bootstrap service extracted from the BotBoy orchestrator.

This module mirrors the initialization sequence that currently lives in
``botboy.__main__`` so it can be tested and later integrated without changing
the orchestrator yet.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Optional

from botboy.a2a_pilot import A2APilotAdapter, BoundedA2APilotRegistry
from botboy.agent_skills import AgentSkillLibrary
from botboy.archetypes import ArchetypeDatabase
from botboy.channels import ChannelAdapter, FiveChannelRouter, get_router
from botboy.core.config import BotBoyConfig
from botboy.delegation_advisor import DelegationAdvisor
from botboy.llm.integration import LLMIntegration
from botboy.llm.parallel_think import ParallelThinkEngine
from botboy.llm.reflection import ReflectionEngine
from botboy.monitoring import PerformanceMonitor
from botboy.metrics import MetricsCollector
from botboy.memory.simple import SimpleMemoryEngine
from botboy.planning import PlanningEngine
from botboy.reflection_memory import ReflectionMemoryArchive
from botboy.scheduler import BotBoyScheduler
from botboy.skills.manager import SkillManager
from botboy.tasks import TaskStore
from botboy.tracing import TraceStore
from botboy.validation import InputValidator
from botboy.cache import ResponseCache
from botboy.history import TaskHistory
from botboy.core.dag_engine import MassEscalationEngine
from botboy.core.self_healing import SelfHealingEngine
from botboy.security.governance import GovernanceEngine


class BootstrapService:
    """Standalone bootstrapper for BotBoy core services."""

    def __init__(self, bot: Any) -> None:
        self.bot = bot

    @property
    def config(self) -> BotBoyConfig:
        return self.bot.config

    def initialize(self) -> bool:
        """Initialise the core runtime components on the bot object."""
        try:
            from botboy.observability import configure_json_logging
            configure_json_logging()
            
            self.bot.monitor = PerformanceMonitor(
                retention=self.config.performance.metrics_retention
            )

            self.bot.metrics = MetricsCollector.get()
            self.bot.validator = InputValidator(mode=InputValidator.NORMAL)

            if self.config.cache.enabled:
                self.bot.cache = ResponseCache(max_size=self.config.cache.max_size)

            db_path = self.config.resolve_memory_db_path()
            self.bot.memory = SimpleMemoryEngine(db_path)

            skills_dir = self.config.resolve_skills_dir()
            self.bot.skills = SkillManager(
                skills_dir=skills_dir,
                auto_discover=self.config.skills.auto_discover,
                validate_security=self.config.skills.validate_security,
            )
            self.bot.skills.load_all_skills()

            try:
                self.bot.agent_skill_library = AgentSkillLibrary.load()
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                self.bot.agent_skill_library = None

            if self.config.history.enabled:
                hist_path = self.config.resolve_history_db_path()
                self.bot.history = TaskHistory(db_path=hist_path)

            if getattr(self.config.trace, "enabled", True):
                trace_path = self.config.resolve_trace_db_path()
                for candidate in (trace_path, ":memory:"):
                    try:
                        self.bot.trace_store = TraceStore(db_path=candidate)
                        break
                    except (OSError, RuntimeError):
                        continue

            if getattr(self.config.tasks, "enabled", True):
                task_db_path = self.config.resolve_task_db_path()
                task_artifact_root = self.config.resolve_task_artifact_root()
                for candidate in (task_db_path, ":memory:"):
                    try:
                        self.bot.task_store = TaskStore(
                            db_path=candidate,
                            artifact_root=task_artifact_root,
                        )
                        break
                    except (OSError, RuntimeError):
                        continue
                if hasattr(self.bot, 'task_store') and self.bot.task_store:
                    self.bot.dag_engine = MassEscalationEngine(self.bot.task_store)
                    self.bot.self_healing = SelfHealingEngine(self.bot.task_store)
                    self.bot.governance = GovernanceEngine(self.bot.task_store)

            if self.config.scheduler.enabled:
                sched_path = self.config.resolve_scheduler_db_path()
                self.bot.scheduler = BotBoyScheduler(db_path=sched_path)
                run_task = getattr(self.bot, "_run_scheduled_task", None)
                if run_task is not None:
                    self.bot.scheduler.register_handler("generic", run_task)
                    self.bot.scheduler.register_handler("command", run_task)
                self.bot.scheduler.start()

            reflection_db_path = ":memory:"
            if db_path != ":memory:":
                try:
                    reflection_db_path = str(
                        Path(db_path).expanduser().with_name("reflection_memory.db")
                    )
                except (OSError, RuntimeError, ValueError):
                    reflection_db_path = ":memory:"

            self.bot.reflection_archive = ReflectionMemoryArchive(reflection_db_path)
            self.bot.delegation_advisor = DelegationAdvisor(self.bot.workers.list_workers())
            self.bot.a2a_pilot = BoundedA2APilotRegistry(max_payload_bytes=4096)
            self._register_default_a2a_adapters()

            self.bot.router = get_router()
            self.bot.archetypes = ArchetypeDatabase(db_path=":memory:")
            self.bot.channel_adapter = ChannelAdapter(self.bot, self.bot.router)

            if self.config.memory.engine == "hybrid":
                try:
                    from botboy.memory.hybrid import HybridMemoryEngine

                    self.bot.memory = HybridMemoryEngine(
                        db_path=self.config.resolve_memory_db_path()
                    )
                    self.bot._hybrid_memory = True
                except ImportError:
                    pass

            if self.config.llm.enabled:
                self._init_llm()

            security = getattr(self.config, "security", None)
            enable_auth = getattr(security, "enable_auth", False) if security else False
            if not enable_auth:
                import logging
                import warnings
                _auth_msg = (
                    "[SECURITY WARNING] BotBoy auth is DISABLED. All API endpoints are publicly accessible. "
                    "Set security.enable_auth=true in config or BOTBOY_ENABLE_AUTH=true for production."
                )
                warnings.warn(_auth_msg, stacklevel=2)
                logging.warning(_auth_msg)

            self.bot._initialized = True
            return True
        except Exception as exc:  # pragma: no cover - defensive parity with __main__
            import traceback
            traceback.print_exc(file=sys.stderr)
            print(f"[BotBoy] Initialisation error: {exc}", file=sys.stderr)
            return False

    def _init_llm(self) -> None:
        """Initialise LLM integration and optional AI engines."""
        try:
            self.bot.llm = LLMIntegration.from_config(self.config.llm)

            if self.config.llm.reflection_enabled:
                self.bot.reflection = ReflectionEngine(
                    llm=self.bot.llm, max_rounds=2, threshold=0.75, enabled=True
                )

            if self.config.llm.planning_enabled:
                self.bot.planner = PlanningEngine(
                    llm=self.bot.llm, skills=self.bot.skills, max_rounds=2
                )

            if self.config.llm.parallel_think_enabled:
                self.bot.parallel_thinker = ParallelThinkEngine(
                    llm=self.bot.llm, max_steps=4, threshold=0.6
                )
        except ImportError as exc:  # pragma: no cover - mirrors current orchestrator
            print(f"[BotBoy] LLM init warning: {exc}", file=sys.stderr)

    def _register_default_a2a_adapters(self) -> None:
        a2a_pilot = getattr(self.bot, "a2a_pilot", None)
        if not a2a_pilot:
            return

        adapter_specs = [
            ("planner-local", "Planner Local Adapter", ("planning", "triage", "handoff")),
            ("review-local", "Review Local Adapter", ("verification", "merge_review", "release")),
            (
                "executor-local",
                "Executor Local Adapter",
                ("coding", "artifact_generation", "implementation"),
            ),
        ]
        for adapter_id, name, capabilities in adapter_specs:
            if a2a_pilot.get(adapter_id):
                continue

            def handler(message, *, _adapter_id=adapter_id, _caps=capabilities):
                return {
                    "status": "ok",
                    "payload": {
                        "adapter_id": _adapter_id,
                        "task_id": message.task_id,
                        "principal": message.principal,
                        "received": dict(message.payload),
                    },
                    "metadata": {
                        "bounded": True,
                        "capabilities": list(_caps),
                    },
                }

            a2a_pilot.register(
                A2APilotAdapter(
                    adapter_id=adapter_id,
                    name=name,
                    handler=handler,
                    capabilities=list(capabilities),
                )
            )


def bootstrap_bot(bot: Any) -> bool:
    """Convenience wrapper for callers that want the bootstrap result only."""
    return BootstrapService(bot).initialize()

