"""Governance Layer — Autonomy Envelopes and Incident Playbooks for BotBoy V4."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import botboy.tasks as btasks

logger = logging.getLogger(__name__)


@dataclass
class AutonomyEnvelope:
    org_id: str
    max_budget_dollars: float = 10.0
    current_spend_dollars: float = 0.0
    max_parallel_tasks: int = 5
    allowed_risk_level: str = "medium"
    forbidden_skills: List[str] = field(default_factory=list)

    @property
    def is_exhausted(self) -> bool:
        return self.current_spend_dollars >= self.max_budget_dollars

    def can_execute_skill(self, skill_name: str, risk: str) -> bool:
        if skill_name in self.forbidden_skills:
            return False
        
        levels = {"low": 1, "medium": 2, "high": 3, "critical": 4}
        if levels.get(risk.lower(), 4) > levels.get(self.allowed_risk_level.lower(), 2):
            return False
            
        return True


class GovernanceEngine:
    """Enforces execution limits and provides Incident Playbook interventions."""

    def __init__(self, task_store: btasks.TaskStore) -> None:
        self.envelopes: Dict[str, AutonomyEnvelope] = {}
        self.store = task_store
        
    def set_envelope(self, envelope: AutonomyEnvelope) -> None:
        self.envelopes[envelope.org_id] = envelope

    def get_envelope(self, org_id: str) -> AutonomyEnvelope:
        if org_id not in self.envelopes:
            self.envelopes[org_id] = AutonomyEnvelope(org_id=org_id)
        return self.envelopes[org_id]

    def enforce_pre_execution(self, org_id: str, skill_name: str, risk: str) -> bool:
        """Called before a tool or sub-agent is launched."""
        env = self.get_envelope(org_id)
        if env.is_exhausted:
            logger.warning(f"Governance block: Org {org_id} has exhausted budget.")
            return False
        if not env.can_execute_skill(skill_name, risk):
            logger.warning(f"Governance block: Skill {skill_name} or risk {risk} forbidden for Org {org_id}.")
            return False
            
        # Check parallel tasks limit
        active = self.store._get_conn().execute(
            "SELECT COUNT(*) as cnt FROM tasks WHERE org_id = ? AND status IN (?, ?)",
            (org_id, btasks.TASK_STATUS_QUEUED, btasks.TASK_STATUS_RUNNING)
        ).fetchone()
        
        if active and active["cnt"] >= env.max_parallel_tasks:
            logger.warning(f"Governance block: Org {org_id} hit max parallel tasks ({env.max_parallel_tasks}).")
            return False

        return True

    def incident_playbook_pause(self, root_task_id: str, reason: str = "Incident intervention") -> int:
        """Pause all executing tasks under a specific root task."""
        conn = self.store._get_conn()
        now = datetime.now(timezone.utc).isoformat()
        res = conn.execute(
            "UPDATE tasks SET status = ?, updated_at = ? WHERE root_task_id = ? AND status IN (?, ?)",
            (btasks.TASK_STATUS_BLOCKED, now, root_task_id, btasks.TASK_STATUS_QUEUED, btasks.TASK_STATUS_RUNNING)
        )
        conn.commit()
        if res.rowcount > 0:
            logger.critical(f"Incident [PAUSE]: {res.rowcount} tasks paused for root {root_task_id}. Reason: {reason}")
        return res.rowcount

    def incident_playbook_quarantine(self, worker_id: str, reason: str = "Worker compromised") -> int:
        """Quarantine a malfunctioning worker globally across the fabric."""
        conn = self.store._get_conn()
        now = datetime.now(timezone.utc).isoformat()
        
        # 1. Drain the worker node
        res1 = conn.execute(
            "UPDATE worker_nodes SET node_status = 'quarantined', drain_state = 'draining', updated_at = ? WHERE worker_id = ?",
            (now, worker_id)
        )
        
        # 2. Re-queue tasks assigned to this worker
        res2 = conn.execute(
            "UPDATE tasks SET delegated_to_worker = '', status = ?, updated_at = ? WHERE delegated_to_worker = ? AND status IN (?, ?)",
            (btasks.TASK_STATUS_QUEUED, now, worker_id, btasks.TASK_STATUS_RUNNING, btasks.TASK_STATUS_QUEUED)
        )
        conn.commit()
        logger.critical(f"Incident [QUARANTINE]: Worker {worker_id} drained ({res1.rowcount} nodes). {res2.rowcount} tasks reassigned. Reason: {reason}")
        return res2.rowcount

    def incident_playbook_rollback(self, task_id: str) -> bool:
        """Rolls a task back to its queued state to be tried again safely."""
        conn = self.store._get_conn()
        now = datetime.now(timezone.utc).isoformat()
        res = conn.execute(
            "UPDATE tasks SET status = ?, delegated_to_worker = '', updated_at = ? WHERE task_id = ?",
            (btasks.TASK_STATUS_QUEUED, now, task_id)
        )
        conn.commit()
        if res.rowcount > 0:
            logger.info(f"Incident [ROLLBACK]: Task {task_id} rolled back to queue.")
            return True
        return False
