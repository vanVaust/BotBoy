"""Fixed internal worker registry for BotBoy Welle 8."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class WorkerProfile:
    worker_id: str
    display_name: str
    role: str
    capabilities: List[str]
    max_concurrency: int = 1
    approval_profile: str = "standard"

    def to_dict(self) -> dict:
        return {
            "worker_id": self.worker_id,
            "display_name": self.display_name,
            "role": self.role,
            "capabilities": list(self.capabilities),
            "max_concurrency": self.max_concurrency,
            "approval_profile": self.approval_profile,
        }


class WorkerRegistry:
    """Canonical internal worker registry for fixed Welle-8 workers."""

    def __init__(self) -> None:
        self._workers: Dict[str, WorkerProfile] = {
            "planner": WorkerProfile(
                worker_id="planner",
                display_name="Planner",
                role="strategy",
                capabilities=["breakdown", "sequencing", "handoff_planning", "merge_preparation"],
                max_concurrency=2,
                approval_profile="planning",
            ),
            "researcher": WorkerProfile(
                worker_id="researcher",
                display_name="Researcher",
                role="analysis",
                capabilities=["context_gathering", "source_synthesis", "risk_mapping", "requirements"],
                max_concurrency=2,
                approval_profile="read_heavy",
            ),
            "executor": WorkerProfile(
                worker_id="executor",
                display_name="Executor",
                role="implementation",
                capabilities=["coding", "tool_execution", "workflow_steps", "artifact_generation"],
                max_concurrency=3,
                approval_profile="write_paths",
            ),
            "reviewer": WorkerProfile(
                worker_id="reviewer",
                display_name="Reviewer",
                role="quality",
                capabilities=["verification", "regression_checks", "safety_review", "merge_review"],
                max_concurrency=2,
                approval_profile="review",
            ),
            "designer": WorkerProfile(
                worker_id="designer",
                display_name="Designer",
                role="ux",
                capabilities=["control_center", "ui_structure", "dashboard_readability", "design_review"],
                max_concurrency=1,
                approval_profile="ui",
            ),
        }

    def list_workers(self) -> List[WorkerProfile]:
        return [self._workers[key] for key in sorted(self._workers)]

    def get(self, worker_id: str) -> Optional[WorkerProfile]:
        return self._workers.get((worker_id or "").strip().lower())

    def exists(self, worker_id: str) -> bool:
        return self.get(worker_id) is not None

