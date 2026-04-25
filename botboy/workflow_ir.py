"""Workflow IR, policy decision records, and replay events for v3 runtime hooks."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence


RISKY_COMMAND_PREFIXES = (
    "a2a dispatch",
    "handoff ",
    "memory delete",
    "scheduler add",
    "schedule add",
    "skill ",
    "task merge apply",
    "task merge reapply",
    "worker node drain",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_suffix(*parts: Any, length: int = 16) -> str:
    raw = "\n".join(str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, Iterable):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


@dataclass(frozen=True)
class WorkflowIRIssue:
    code: str
    message: str
    step_id: str = ""
    severity: str = "error"

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "step_id": self.step_id,
            "severity": self.severity,
        }


@dataclass
class WorkflowStepIR:
    step_id: str
    command: str
    description: str = ""
    depends_on: list[str] = field(default_factory=list)
    request_id: str = ""
    approval_context: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "command": self.command,
            "description": self.description,
            "depends_on": list(self.depends_on),
            "request_id": self.request_id,
            "approval_context": dict(self.approval_context),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "WorkflowStepIR":
        return cls(
            step_id=str(payload.get("step_id") or payload.get("id") or "").strip(),
            command=str(payload.get("command") or payload.get("input") or "").strip(),
            description=str(payload.get("description") or "").strip(),
            depends_on=_as_list(payload.get("depends_on")),
            request_id=str(payload.get("request_id") or "").strip(),
            approval_context=_as_dict(payload.get("approval_context")),
            metadata=_as_dict(payload.get("metadata")),
        )


@dataclass
class PolicyDecisionRecord:
    decision_id: str
    workflow_id: str
    step_id: str
    surface: str
    action: str
    principal: str
    roles: list[str]
    allowed: bool
    reason: str
    approval_required: bool = False
    approval_granted: bool = False
    capabilities: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "workflow_id": self.workflow_id,
            "step_id": self.step_id,
            "surface": self.surface,
            "action": self.action,
            "principal": self.principal,
            "roles": list(self.roles),
            "allowed": self.allowed,
            "reason": self.reason,
            "approval_required": self.approval_required,
            "approval_granted": self.approval_granted,
            "capabilities": list(self.capabilities),
            "evidence": dict(self.evidence),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PolicyDecisionRecord":
        return cls(
            decision_id=str(payload.get("decision_id") or "").strip(),
            workflow_id=str(payload.get("workflow_id") or "").strip(),
            step_id=str(payload.get("step_id") or "").strip(),
            surface=str(payload.get("surface") or "").strip(),
            action=str(payload.get("action") or "").strip(),
            principal=str(payload.get("principal") or "").strip(),
            roles=_as_list(payload.get("roles")),
            allowed=bool(payload.get("allowed", False)),
            reason=str(payload.get("reason") or "").strip(),
            approval_required=bool(payload.get("approval_required", False)),
            approval_granted=bool(payload.get("approval_granted", False)),
            capabilities=_as_list(payload.get("capabilities")),
            evidence=_as_dict(payload.get("evidence")),
            created_at=str(payload.get("created_at") or _utc_now()),
        )


@dataclass
class WorkflowReplayEvent:
    event_id: str
    workflow_id: str
    event_type: str
    step_id: str = ""
    status: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "workflow_id": self.workflow_id,
            "event_type": self.event_type,
            "step_id": self.step_id,
            "status": self.status,
            "payload": dict(self.payload),
            "created_at": self.created_at,
        }


@dataclass
class WorkflowIR:
    workflow_id: str
    goal: str
    steps: list[WorkflowStepIR]
    principal: str = "anonymous"
    request_id: str = ""
    version: str = "workflow-ir/v1"
    policy_decisions: list[PolicyDecisionRecord] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "version": self.version,
            "goal": self.goal,
            "principal": self.principal,
            "request_id": self.request_id,
            "steps": [step.to_dict() for step in self.steps],
            "policy_decisions": [decision.to_dict() for decision in self.policy_decisions],
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "WorkflowIR":
        return cls(
            workflow_id=str(payload.get("workflow_id") or "").strip(),
            version=str(payload.get("version") or "workflow-ir/v1").strip(),
            goal=str(payload.get("goal") or "").strip(),
            principal=str(payload.get("principal") or "anonymous").strip(),
            request_id=str(payload.get("request_id") or "").strip(),
            steps=[WorkflowStepIR.from_dict(item) for item in payload.get("steps", []) if isinstance(item, Mapping)],
            policy_decisions=[
                PolicyDecisionRecord.from_dict(item)
                for item in payload.get("policy_decisions", [])
                if isinstance(item, Mapping)
            ],
            metadata=_as_dict(payload.get("metadata")),
            created_at=str(payload.get("created_at") or _utc_now()),
        )

    def validate(self) -> list[WorkflowIRIssue]:
        issues: list[WorkflowIRIssue] = []
        if not self.workflow_id:
            issues.append(WorkflowIRIssue("missing_workflow_id", "Workflow id is required."))
        if not self.steps:
            issues.append(WorkflowIRIssue("empty_workflow", "Workflow contains no steps."))

        seen: set[str] = set()
        step_ids: set[str] = set()
        for index, step in enumerate(self.steps):
            if not step.step_id:
                issues.append(WorkflowIRIssue("missing_step_id", "Step id is required.", severity="error"))
            elif step.step_id in seen:
                issues.append(WorkflowIRIssue("duplicate_step_id", "Step id is duplicated.", step_id=step.step_id))
            seen.add(step.step_id)
            step_ids.add(step.step_id)

            if not step.command:
                issues.append(WorkflowIRIssue("empty_command", "Step command is required.", step_id=step.step_id or f"s{index + 1}"))

        for step in self.steps:
            for dep in step.depends_on:
                if dep not in step_ids:
                    issues.append(
                        WorkflowIRIssue(
                            "missing_dependency",
                            f"Dependency '{dep}' does not exist.",
                            step_id=step.step_id,
                        )
                    )

        issues.extend(self._cycle_issues())
        return issues

    def _cycle_issues(self) -> list[WorkflowIRIssue]:
        by_id = {step.step_id: step for step in self.steps if step.step_id}
        visiting: set[str] = set()
        visited: set[str] = set()
        issues: list[WorkflowIRIssue] = []

        def visit(step_id: str, path: list[str]) -> None:
            if step_id in visited:
                return
            if step_id in visiting:
                cycle = " -> ".join(path + [step_id])
                issues.append(WorkflowIRIssue("cyclic_dependency", f"Workflow dependency cycle: {cycle}", step_id=step_id))
                return
            visiting.add(step_id)
            for dep in by_id.get(step_id, WorkflowStepIR(step_id, "")).depends_on:
                if dep in by_id:
                    visit(dep, path + [step_id])
            visiting.discard(step_id)
            visited.add(step_id)

        for step_id in by_id:
            visit(step_id, [])
        return issues


class WorkflowReplayRecorder:
    """Small append-only recorder used by evals and future deterministic replay."""

    def __init__(self, workflow_id: str, *, clock=None) -> None:
        self.workflow_id = workflow_id
        self._clock = clock or _utc_now
        self.events: list[WorkflowReplayEvent] = []

    def record(
        self,
        event_type: str,
        *,
        step_id: str = "",
        status: str = "",
        payload: Mapping[str, Any] | None = None,
    ) -> WorkflowReplayEvent:
        event_id = f"evt-{len(self.events) + 1:04d}-{_stable_suffix(self.workflow_id, event_type, step_id, len(self.events))}"
        event = WorkflowReplayEvent(
            event_id=event_id,
            workflow_id=self.workflow_id,
            event_type=str(event_type),
            step_id=str(step_id or ""),
            status=str(status or ""),
            payload=dict(payload or {}),
            created_at=str(self._clock()),
        )
        self.events.append(event)
        return event

    def to_dicts(self) -> list[dict[str, Any]]:
        return [event.to_dict() for event in self.events]


def workflow_step_from_spec(
    spec: Any,
    *,
    index: int,
    default_request_id: str = "",
) -> WorkflowStepIR:
    if isinstance(spec, str):
        return WorkflowStepIR(
            step_id=f"s{index + 1}",
            command=spec.strip(),
            request_id=default_request_id,
        )
    if isinstance(spec, Mapping):
        payload = dict(spec)
        payload.setdefault("step_id", payload.get("id") or f"s{index + 1}")
        payload.setdefault("request_id", default_request_id)
        step = WorkflowStepIR.from_dict(payload)
        if not step.step_id:
            step.step_id = f"s{index + 1}"
        if not step.request_id:
            step.request_id = default_request_id
        return step

    step_id = getattr(spec, "step_id", "") or getattr(spec, "id", "") or f"s{index + 1}"
    command = getattr(spec, "command", "") or str(spec)
    return WorkflowStepIR(
        step_id=str(step_id).strip(),
        command=str(command).strip(),
        description=str(getattr(spec, "description", "") or "").strip(),
        depends_on=_as_list(getattr(spec, "depends_on", [])),
        request_id=default_request_id,
        metadata={"source_type": type(spec).__name__},
    )


def compile_workflow_ir(
    *,
    goal: str,
    step_specs: Sequence[Any],
    principal: str = "anonymous",
    request_id: str = "",
    workflow_id: str = "",
    metadata: Mapping[str, Any] | None = None,
) -> WorkflowIR:
    stable_workflow_id = workflow_id or f"wf-{_stable_suffix(goal, principal, request_id, len(step_specs))}"
    return WorkflowIR(
        workflow_id=stable_workflow_id,
        goal=str(goal or "").strip(),
        principal=str(principal or "anonymous").strip(),
        request_id=str(request_id or "").strip(),
        steps=[
            workflow_step_from_spec(step_spec, index=index, default_request_id=request_id)
            for index, step_spec in enumerate(step_specs)
        ],
        metadata=dict(metadata or {}),
    )


def command_requires_approval(command: str) -> bool:
    normalized = " ".join(str(command or "").strip().lower().split())
    return any(normalized.startswith(prefix) for prefix in RISKY_COMMAND_PREFIXES)


def approval_granted(approval_context: Mapping[str, Any] | None, roles: Sequence[str] | None = None) -> bool:
    context = dict(approval_context or {})
    normalized_roles = {str(role).lower() for role in (roles or [])}
    return bool(
        context.get("granted")
        or context.get("approved")
        or context.get("approval")
        or context.get("allow")
        or "admin" in normalized_roles
    )


def build_policy_decision(
    *,
    workflow_id: str,
    step: WorkflowStepIR,
    principal: str,
    roles: Sequence[str] | None = None,
    approval_context: Mapping[str, Any] | None = None,
    surface: str = "workflow_replay",
) -> PolicyDecisionRecord:
    context = dict(approval_context or {})
    required = bool(context.get("needs_approval") or command_requires_approval(step.command))
    granted = approval_granted(context, roles)
    allowed = (not required) or granted
    reason = "approval_granted" if required and granted else "approval_required" if required else "policy_allowed"
    capabilities = ["command-execution"]
    if required:
        capabilities.append("approval-gated")
    decision_id = f"pdr-{_stable_suffix(workflow_id, step.step_id, step.command, principal, required, granted)}"
    return PolicyDecisionRecord(
        decision_id=decision_id,
        workflow_id=workflow_id,
        step_id=step.step_id,
        surface=surface,
        action=step.command,
        principal=principal or "anonymous",
        roles=[str(role) for role in (roles or [])],
        allowed=allowed,
        reason=reason,
        approval_required=required,
        approval_granted=granted,
        capabilities=capabilities,
        evidence={
            "command_prefix_matched": command_requires_approval(step.command),
            "approval_context": context,
        },
    )

