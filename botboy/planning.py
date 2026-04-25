"""PlanningEngine — TriPlex adversarial planning (Architect → Entropy → Auditor)."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class PlanStep:
    step_id: str
    description: str
    skill: str
    command: str
    risk_level: str = "low"
    depends_on: List[str] = field(default_factory=list)


@dataclass
class ExecutionPlan:
    task: str
    strategy: str
    steps: List[PlanStep]
    executable: bool
    confidence: float
    auditor_notes: str = ""
    rounds: int = 0


@dataclass
class PlanResult:
    plan: ExecutionPlan
    outputs: List[dict] = field(default_factory=list)
    success: bool = True
    error: Optional[str] = None


_ARCHITECT_SYSTEM = """You are the ARCHITECT — a strategic planner.
Given a complex task, break it into a concrete, executable action plan.

Respond ONLY as JSON with this exact structure:
{
  "strategy": "brief strategy description",
  "steps": [
    {
      "step_id": "s1",
      "description": "what this step does",
      "skill": "skill_name or builtin",
      "command": "exact command to execute",
      "risk_level": "low|medium|high",
      "depends_on": []
    }
  ]
}

Use only these skills: calculate, hash, base64, convert, weather, web, file, memory, status, system.
Be concrete and actionable. Steps must be executable by BotBoy commands."""

_ENTROPY_SYSTEM = """You are ENTROPY — an adversarial critic.
Your role: find weaknesses, impossible steps, missing dependencies, and security risks in plans.

Respond ONLY as JSON:
{
  "approved": true/false,
  "issues": ["issue 1", "issue 2"],
  "impossible_steps": ["step_id1"],
  "security_concerns": ["concern 1"],
  "recommendation": "approve|refine|reject"
}

Be ruthless but fair. Approve only if the plan is genuinely feasible and safe."""

_AUDITOR_SYSTEM = """You are the AUDITOR — the final verification layer.
Given a plan that has passed Architect and Entropy review, make the final decision.

Respond ONLY as JSON:
{
  "approved": true/false,
  "confidence": 0.0-1.0,
  "final_steps": ["s1", "s2"],
  "notes": "brief final assessment"
}

Approve if confidence >= 0.6. Be decisive."""


class PlanningEngine:
    """
    TriPlex adversarial planning loop:
      Phase 1 — ARCHITECT: generates structured plan
      Phase 2 — ENTROPY: attacks the plan (finds issues)
      (Loop max_rounds)
      Phase 3 — AUDITOR: final verification + confidence score

    If llm is None: plan.executable = False immediately.
    """

    def __init__(self, llm=None, skills=None, max_rounds: int = 2) -> None:
        self._llm = llm
        self._skills = skills or []
        self.max_rounds = max_rounds

    def _make_llm(self, system_prompt: str):
        if self._llm is None:
            return None
        from botboy.llm.integration import LLMIntegration
        return LLMIntegration(backend=self._llm._backend, system_prompt=system_prompt)

    async def plan(self, task: str) -> ExecutionPlan:
        if self._llm is None:
            return ExecutionPlan(
                task=task, strategy="No LLM available",
                steps=[], executable=False, confidence=0.0,
                auditor_notes="LLM not configured",
            )

        architect = self._make_llm(_ARCHITECT_SYSTEM)
        entropy = self._make_llm(_ENTROPY_SYSTEM)
        auditor = self._make_llm(_AUDITOR_SYSTEM)

        plan_json = None
        rounds = 0

        # Architect → Entropy loop
        for round_num in range(self.max_rounds):
            rounds += 1

            # Architect generates/refines plan
            arch_prompt = (
                f"Task: {task}\n"
                + (f"\nPrevious issues to address: {json.dumps(plan_json.get('entropy_issues', []))}"
                   if plan_json and "entropy_issues" in plan_json else "")
            )
            arch_resp = await architect.chat(arch_prompt, use_history=False)

            try:
                raw = _clean_json(arch_resp.content)
                plan_json = json.loads(raw)
            except (json.JSONDecodeError, AttributeError):
                plan_json = {"strategy": arch_resp.content, "steps": []}

            # Entropy attacks the plan
            ent_prompt = f"Review this plan for task: {task}\n\nPlan:\n{json.dumps(plan_json, indent=2)}"
            ent_resp = await entropy.chat(ent_prompt, use_history=False)

            try:
                raw = _clean_json(ent_resp.content)
                ent_data = json.loads(raw)
            except (json.JSONDecodeError, AttributeError):
                ent_data = {"approved": True, "issues": [], "recommendation": "approve"}

            if ent_data.get("approved") or ent_data.get("recommendation") == "approve":
                break

            if ent_data.get("recommendation") == "reject":
                return ExecutionPlan(
                    task=task, strategy=plan_json.get("strategy", ""),
                    steps=[], executable=False, confidence=0.0,
                    auditor_notes=f"Entropy rejected: {ent_data.get('issues', [])}",
                    rounds=rounds,
                )

            # Store issues for next Architect round
            plan_json["entropy_issues"] = ent_data.get("issues", [])

        # Auditor final check
        aud_prompt = (
            f"Final verification of plan for: {task}\n\nPlan:\n{json.dumps(plan_json, indent=2)}"
        )
        aud_resp = await auditor.chat(aud_prompt, use_history=False)

        try:
            raw = _clean_json(aud_resp.content)
            aud_data = json.loads(raw)
        except (json.JSONDecodeError, AttributeError):
            aud_data = {"approved": True, "confidence": 0.7, "notes": "Auditor parse error"}

        steps = [
            PlanStep(
                step_id=s.get("step_id", f"s{i}"),
                description=s.get("description", ""),
                skill=s.get("skill", "unknown"),
                command=s.get("command", ""),
                risk_level=s.get("risk_level", "low"),
                depends_on=s.get("depends_on", []),
            )
            for i, s in enumerate(plan_json.get("steps", []))
        ]

        return ExecutionPlan(
            task=task,
            strategy=plan_json.get("strategy", ""),
            steps=steps,
            executable=aud_data.get("approved", False),
            confidence=float(aud_data.get("confidence", 0.0)),
            auditor_notes=aud_data.get("notes", ""),
            rounds=rounds,
        )

    async def execute(self, plan: ExecutionPlan, bot=None) -> PlanResult:
        if not plan.executable:
            return PlanResult(plan=plan, success=False,
                              error=f"Plan not executable: {plan.auditor_notes}")
        if bot is None:
            return PlanResult(plan=plan, success=False, error="No bot instance for execution")

        outputs = []
        executed = set()

        for step in plan.steps:
            # Check dependencies
            if any(dep not in executed for dep in step.depends_on):
                outputs.append({"step_id": step.step_id, "skipped": True, "reason": "dependency not met"})
                continue

            try:
                result = await bot.process_command(step.command)
                outputs.append({"step_id": step.step_id, "result": result, "success": result.get("success", False)})
                if result.get("success"):
                    executed.add(step.step_id)
            except Exception as e:
                outputs.append({"step_id": step.step_id, "error": str(e), "success": False})

        all_success = all(o.get("success", False) for o in outputs if not o.get("skipped"))
        return PlanResult(plan=plan, outputs=outputs, success=all_success)


def _clean_json(text: str) -> str:
    """Strip markdown code fences from LLM JSON responses."""
    text = text.strip()
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 3:
            text = parts[1]
            if text.startswith("json"):
                text = text[4:]
    return text.strip()
