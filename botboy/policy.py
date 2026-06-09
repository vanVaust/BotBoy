"""Policy Engine — Evaluation Contracts and Decision Records for BotBoy V3."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

@dataclass
class EvaluationContract:
    action: str
    required_capabilities: List[str] = field(default_factory=list)
    required_roles: List[str] = field(default_factory=list)
    max_risk_level: str = "medium"
    requires_approval: bool = False
    max_cost: float = 0.0

@dataclass
class DecisionRecord:
    decision_id: str
    target_id: str
    action: str
    org_id: str
    principal: str
    allowed: bool
    reason: str
    risk_level: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "decision_id": self.decision_id,
            "target_id": self.target_id,
            "action": self.action,
            "org_id": self.org_id,
            "principal": self.principal,
            "allowed": self.allowed,
            "reason": self.reason,
            "risk_level": self.risk_level,
            "timestamp": self.timestamp,
        }

class PolicyEngine:
    """Evaluates agent actions against security and orchestration constraints."""

    _RISK_LEVELS = {"low": 1, "medium": 2, "high": 3, "critical": 4}

    def __init__(self):
        self.contracts: Dict[str, EvaluationContract] = {}
        self.decision_log: List[DecisionRecord] = []

    def set_contract(self, contract: EvaluationContract) -> None:
        self.contracts[contract.action] = contract

    def evaluate_action(
        self,
        action: str,
        target_id: str,
        org_id: str,
        principal: str,
        worker_roles: List[str],
        worker_capabilities: List[str],
        risk_level: str = "low",
        has_approval: bool = False,
    ) -> DecisionRecord:
        import uuid
        decision_id = f"dec-{uuid.uuid4().hex[:8]}"

        contract = self.contracts.get(action)
        if not contract:
            # Default fail-closed policy
            record = DecisionRecord(
                decision_id=decision_id, target_id=target_id, action=action,
                org_id=org_id, principal=principal, allowed=False,
                reason="No active contract for action", risk_level=risk_level
            )
            self.decision_log.append(record)
            return record

        try:
            req_risk = self._RISK_LEVELS.get(contract.max_risk_level.lower(), 2)
            act_risk = self._RISK_LEVELS.get(risk_level.lower(), 4)
            if act_risk > req_risk:
                raise PermissionError(f"Action risk '{risk_level}' exceeds maximum allowed '{contract.max_risk_level}'")

            if contract.requires_approval and not has_approval:
                raise PermissionError("Explicit human or orchestrator approval required")

            if contract.required_roles:
                if not any(role in contract.required_roles for role in worker_roles):
                    raise PermissionError(f"Worker missing required roles: {contract.required_roles}")

            if contract.required_capabilities:
                missing = [cap for cap in contract.required_capabilities if cap not in worker_capabilities]
                if missing:
                    raise PermissionError(f"Worker missing capabilities: {missing}")

            record = DecisionRecord(
                decision_id=decision_id, target_id=target_id, action=action,
                org_id=org_id, principal=principal, allowed=True,
                reason="Contract satisfied", risk_level=risk_level
            )

        except PermissionError as e:
            record = DecisionRecord(
                decision_id=decision_id, target_id=target_id, action=action,
                org_id=org_id, principal=principal, allowed=False,
                reason=str(e), risk_level=risk_level
            )

        self.decision_log.append(record)
        return record

    def dump_audit_log(self, org_id: Optional[str] = None) -> List[dict]:
        return [
            rec.to_dict() for rec in self.decision_log 
            if org_id is None or rec.org_id == org_id
        ]
