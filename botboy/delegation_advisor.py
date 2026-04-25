"""Delegation intelligence helpers for BotBoy Welle 20."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

from botboy.workers import WorkerProfile, WorkerRegistry

_ROLE_KEYWORDS = {
    "planner": {"plan", "planning", "breakdown", "sequence", "roadmap", "orchestrate", "handoff", "triage"},
    "researcher": {"research", "analyze", "analysis", "investigate", "source", "context", "summary", "summarize", "requirements", "risk"},
    "executor": {"implement", "build", "code", "coding", "patch", "ship", "execute", "tool", "artifact"},
    "reviewer": {"review", "verify", "verification", "audit", "regression", "test", "acceptance", "merge", "quality", "safety"},
    "designer": {"ui", "ux", "dashboard", "surface", "layout", "visual", "design", "control", "readability"},
}

_ROLE_ALIASES = {
    "strategy": "planner",
    "analysis": "researcher",
    "implementation": "executor",
    "quality": "reviewer",
    "ux": "designer",
}

_ROLE_PLAN_STEPS = {
    "planner": [
        "Freeze the objective and split the work into bounded subtasks.",
        "Assign clear handoff and review points for each subtask.",
        "Merge the outputs into a single delivery plan.",
    ],
    "researcher": [
        "Collect the relevant context, constraints, and source signals.",
        "Summarize the risks, dependencies, and open questions.",
        "Package the findings for the executor and reviewer.",
    ],
    "executor": [
        "Implement the smallest safe change set that moves the task forward.",
        "Run the local verification path that exercises the new behavior.",
        "Capture artifacts and hand off the result for review.",
    ],
    "reviewer": [
        "Inspect the change set for correctness, safety, and regressions.",
        "Validate the evidence from tests, traces, or artifacts.",
        "Approve, request fixes, or point to the next smallest improvement.",
    ],
    "designer": [
        "Map the user-facing surface and the information hierarchy.",
        "Refine the layout for clarity, readability, and flow.",
        "Check the design against the existing product language.",
    ],
}


@dataclass(frozen=True)
class WorkerFit:
    worker_id: str
    score: float
    reasons: List[str] = field(default_factory=list)
    matched_capabilities: List[str] = field(default_factory=list)
    missing_capabilities: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "worker_id": self.worker_id,
            "score": self.score,
            "reasons": list(self.reasons),
            "matched_capabilities": list(self.matched_capabilities),
            "missing_capabilities": list(self.missing_capabilities),
        }


@dataclass(frozen=True)
class DelegationPlanStep:
    step_id: str
    worker_id: str
    action: str
    rationale: str
    required_capabilities: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "step_id": self.step_id,
            "worker_id": self.worker_id,
            "action": self.action,
            "rationale": self.rationale,
            "required_capabilities": list(self.required_capabilities),
        }


@dataclass(frozen=True)
class DelegationAdvice:
    chosen_worker: str
    ranked_workers: List[WorkerFit]
    plan: List[DelegationPlanStep]
    rationale: str
    blocked_workers: List[str] = field(default_factory=list)
    missing_capabilities: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "chosen_worker": self.chosen_worker,
            "ranked_workers": [item.to_dict() for item in self.ranked_workers],
            "plan": [item.to_dict() for item in self.plan],
            "rationale": self.rationale,
            "blocked_workers": list(self.blocked_workers),
            "missing_capabilities": list(self.missing_capabilities),
        }


class DelegationAdvisor:
    """Deterministic worker-fit and plan advisor."""

    def __init__(self, workers: Optional[List[WorkerProfile | Mapping[str, Any]]] = None) -> None:
        if workers is None:
            workers = WorkerRegistry().list_workers()
        self._profiles = {item.worker_id: self._normalize_profile(item) for item in workers}

    @staticmethod
    def _canonical_role(role: str) -> str:
        role_key = (role or "").strip().lower()
        return _ROLE_ALIASES.get(role_key, role_key)

    @staticmethod
    def _normalize_profile(worker: WorkerProfile | Mapping[str, Any]) -> Dict[str, Any]:
        if isinstance(worker, WorkerProfile):
            data = worker.to_dict()
            data["role"] = DelegationAdvisor._canonical_role(data.get("role", ""))
            return data
        role = DelegationAdvisor._canonical_role(str(worker.get("role", "")))
        return {
            "worker_id": str(worker.get("worker_id", "")),
            "display_name": str(worker.get("display_name", worker.get("worker_id", ""))),
            "role": role,
            "capabilities": list(worker.get("capabilities", [])),
            "max_concurrency": int(worker.get("max_concurrency", 1) or 1),
            "approval_profile": str(worker.get("approval_profile", "")),
        }

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        return re.findall(r"[a-z0-9_]+", (text or "").lower())

    @staticmethod
    def _profile_terms(profile: Mapping[str, Any]) -> List[str]:
        terms: List[str] = []
        terms.extend(DelegationAdvisor._tokenize(str(profile.get("worker_id", ""))))
        terms.extend(DelegationAdvisor._tokenize(str(profile.get("display_name", ""))))
        terms.extend(DelegationAdvisor._tokenize(str(profile.get("role", ""))))
        for capability in profile.get("capabilities", []):
            terms.extend(DelegationAdvisor._tokenize(str(capability)))
        return terms

    def list_workers(self) -> List[Dict[str, Any]]:
        return [dict(self._profiles[key]) for key in sorted(self._profiles)]

    def get(self, worker_id: str) -> Optional[Dict[str, Any]]:
        return self._profiles.get((worker_id or "").strip())

    def rank_workers(
        self,
        task_text: str,
        *,
        required_capabilities: Sequence[str] = (),
        preferred_worker: str = "",
        blocked_workers: Sequence[str] = (),
        context: Optional[Mapping[str, Any]] = None,
    ) -> List[WorkerFit]:
        blocked = {item.strip() for item in blocked_workers if item and item.strip()}
        required = [item.strip() for item in required_capabilities if item and item.strip()]
        tokens = set(self._tokenize(task_text))
        ranked: List[WorkerFit] = []
        for worker_id, profile in self._profiles.items():
            if worker_id in blocked:
                continue
            ranked.append(
                self._score_worker(
                    worker_id,
                    profile,
                    tokens,
                    required,
                    preferred_worker=preferred_worker,
                    context=context,
                )
            )
        ranked.sort(key=lambda item: (-item.score, item.worker_id))
        return ranked

    def recommend(
        self,
        task_text: str,
        *,
        required_capabilities: Sequence[str] = (),
        preferred_worker: str = "",
        blocked_workers: Sequence[str] = (),
        context: Optional[Mapping[str, Any]] = None,
    ) -> DelegationAdvice:
        ranked = self.rank_workers(
            task_text,
            required_capabilities=required_capabilities,
            preferred_worker=preferred_worker,
            blocked_workers=blocked_workers,
            context=context,
        )
        chosen = ranked[0].worker_id if ranked else ""
        chosen_profile = self._profiles.get(chosen, {})
        missing = ranked[0].missing_capabilities if ranked else []
        rationale = self._build_rationale(chosen_profile, task_text, missing)
        plan = self.draft_plan(task_text, worker_id=chosen)
        return DelegationAdvice(
            chosen_worker=chosen,
            ranked_workers=ranked,
            plan=plan,
            rationale=rationale,
            blocked_workers=[item.strip() for item in blocked_workers if item and item.strip()],
            missing_capabilities=list(missing),
        )

    def recommend_for_task(self, task_text: str, **kwargs: Any) -> DelegationAdvice:
        return self.recommend(task_text, **kwargs)

    def draft_plan(self, task_text: str, *, worker_id: str = "") -> List[DelegationPlanStep]:
        worker = self._profiles.get(worker_id, {})
        role = str(worker.get("role", "")).strip().lower()
        if not role:
            ranked = self.rank_workers(task_text)
            if ranked:
                worker_id = ranked[0].worker_id
                worker = self._profiles.get(worker_id, {})
                role = str(worker.get("role", "")).strip().lower()
        role_key = role if role in _ROLE_PLAN_STEPS else "executor"
        actions = _ROLE_PLAN_STEPS[role_key]
        return [
            DelegationPlanStep(
                step_id=f"{worker_id or role_key}-step-{index + 1}",
                worker_id=worker_id or role_key,
                action=action,
                rationale=self._step_rationale(role_key, action, task_text),
                required_capabilities=self._default_step_capabilities(role_key),
            )
            for index, action in enumerate(actions)
        ]

    def _score_worker(
        self,
        worker_id: str,
        profile: Mapping[str, Any],
        token_set: set[str],
        required_capabilities: Sequence[str],
        *,
        preferred_worker: str = "",
        context: Optional[Mapping[str, Any]] = None,
    ) -> WorkerFit:
        caps = [str(item).strip() for item in profile.get("capabilities", []) if str(item).strip()]
        cap_set = {item.lower() for item in caps}
        required_lower = [item.lower() for item in required_capabilities]
        matched_caps = sorted(cap_set.intersection(required_lower))
        missing_caps = [item for item in required_capabilities if item.lower() not in cap_set]
        score = 0.0
        reasons: List[str] = []
        if preferred_worker and worker_id == preferred_worker:
            score += 10.0
            reasons.append("preferred worker match")
        if matched_caps:
            score += 4.0 * len(matched_caps)
            reasons.append(f"covers required capabilities: {', '.join(matched_caps)}")
        if missing_caps:
            score -= 4.0 * len(missing_caps)
            reasons.append(f"missing capabilities: {', '.join(missing_caps)}")
        terms = set(self._profile_terms(profile))
        overlap = sorted(token_set.intersection(terms))
        if overlap:
            score += 2.0 * len(overlap)
            reasons.append(f"text overlap: {', '.join(overlap[:5])}")
        role = self._canonical_role(str(profile.get("role", "")))
        role_hits = sorted(token_set.intersection(_ROLE_KEYWORDS.get(role, set())))
        if role_hits:
            score += 5.0 + len(role_hits)
            reasons.append(f"role keywords: {', '.join(role_hits[:5])}")
        if context:
            context_role = str(context.get("preferred_role", "")).strip().lower()
            if context_role and context_role == role:
                score += 2.0
                reasons.append(f"context role match: {context_role}")
        if int(profile.get("max_concurrency", 1) or 1) > 1 and any(
            hint in token_set for hint in {"parallel", "batch", "bulk", "fanout", "multi"}
        ):
            score += 1.5
            reasons.append("supports parallel/bulk work")
        return WorkerFit(
            worker_id=worker_id,
            score=round(score, 2),
            reasons=reasons,
            matched_capabilities=matched_caps,
            missing_capabilities=missing_caps,
        )

    @staticmethod
    def _build_rationale(
        profile: Mapping[str, Any],
        task_text: str,
        missing_capabilities: Sequence[str],
    ) -> str:
        worker_name = str(profile.get("display_name", profile.get("worker_id", ""))).strip() or "worker"
        role = str(profile.get("role", "")).strip() or "generalist"
        base = f"{worker_name} is the best fit for this {role} task."
        if missing_capabilities:
            base += f" Missing capabilities should be covered by review or handoff: {', '.join(missing_capabilities)}."
        if task_text:
            base += f" Task focus: {task_text.strip()}."
        return base

    @staticmethod
    def _default_step_capabilities(role_key: str) -> List[str]:
        mapping = {
            "planner": ["breakdown", "sequencing", "handoff_planning"],
            "researcher": ["context_gathering", "source_synthesis"],
            "executor": ["coding", "tool_execution", "artifact_generation"],
            "reviewer": ["verification", "regression_checks", "merge_review"],
            "designer": ["ui_structure", "dashboard_readability"],
        }
        return list(mapping.get(role_key, []))

    @staticmethod
    def _step_rationale(role_key: str, action: str, task_text: str) -> str:
        if role_key == "reviewer":
            return "Keeps the merge or delivery safe before release."
        if role_key == "designer":
            return "Improves the user-facing surface and readability."
        if role_key == "researcher":
            return "Collects the evidence needed to avoid blind implementation."
        if role_key == "planner":
            return "Turns a broad task into bounded work units."
        return f"Supports the core implementation path for: {task_text.strip() or 'task'}."
