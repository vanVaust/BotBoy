"""ParallelThinkEngine — Checkpoint-Coupling: CoT sequential + TriPlex adversarial critic in parallel."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ThinkStep:
    step_num: int
    content: str
    critique: Optional[str] = None
    correction: Optional[str] = None
    confidence: float = 1.0


@dataclass
class ThinkResult:
    question: str
    final_answer: str
    reasoning_trace: str
    steps: List[ThinkStep]
    corrections: int
    total_steps: int
    skipped: bool = False
    skip_reason: str = ""


_COT_SYSTEM = """You are a careful, step-by-step reasoner.
When given a question, think through it one step at a time.
For each step, label it: "Step N: [your reasoning]"
After all steps, provide: "Final Answer: [your conclusion]"
Be thorough but concise."""

_CRITIC_SYSTEM = """You are a critical evaluator of reasoning steps.
Given a reasoning step, identify any errors, false assumptions, or logical gaps.

Respond ONLY as JSON:
{
  "issue_found": true/false,
  "confidence": 0.0-1.0,
  "critique": "specific issue description or 'none'",
  "correction": "how to fix it, or 'none'"
}

Be precise. Only flag real issues, not stylistic preferences."""


class ParallelThinkEngine:
    """
    Checkpoint-Coupling architecture:
      - CoT thread: generates reasoning steps sequentially
      - At each CHECKPOINT: TriPlex critic task is launched in parallel
      - If critic finds issues (confidence < threshold): correction injected into CoT context
      - Result: annotated reasoning trace with corrections marked

    This differs from:
      - Standard CoT: no mid-flight correction
      - ReAct: sequential, no parallel adversarial pressure
      - Tree of Thoughts: parallel but no adversarial critique of each node
    """

    def __init__(
        self,
        llm=None,
        max_steps: int = 4,
        threshold: float = 0.6,
        enabled: bool = True,
    ) -> None:
        self._llm = llm
        self.max_steps = max_steps
        self.threshold = threshold
        self.enabled = enabled

    def _should_skip(self, question: str) -> tuple[bool, str]:
        if not self.enabled:
            return True, "disabled"
        if self._llm is None:
            return True, "no_llm"
        if len(question.strip().split()) < 5:
            return True, "too_short"
        return False, ""

    async def think(self, question: str) -> ThinkResult:
        skip, reason = self._should_skip(question)
        if skip:
            return ThinkResult(
                question=question,
                final_answer=question,
                reasoning_trace="",
                steps=[],
                corrections=0,
                total_steps=0,
                skipped=True,
                skip_reason=reason,
            )

        from botboy.llm.integration import LLMIntegration
        cot = LLMIntegration(backend=self._llm._backend, system_prompt=_COT_SYSTEM)
        critic = LLMIntegration(backend=self._llm._backend, system_prompt=_CRITIC_SYSTEM)

        steps: List[ThinkStep] = []
        corrections = 0
        context_additions: List[str] = []

        for step_num in range(1, self.max_steps + 1):
            # Build CoT prompt with any corrections from previous checkpoints
            step_prompt = f"Question: {question}\n\nGenerate Step {step_num} of your reasoning."
            if context_additions:
                step_prompt += "\n\nNote — apply these corrections from previous steps:\n"
                step_prompt += "\n".join(context_additions)

            cot_resp = await cot.chat(step_prompt, use_history=True)
            step_content = cot_resp.content.strip()

            step = ThinkStep(step_num=step_num, content=step_content)

            # CHECKPOINT: launch critic in parallel (conceptually — we await it)
            critic_resp = await critic.chat(
                f"Evaluate this reasoning step:\nStep {step_num}: {step_content}",
                use_history=False,
            )

            try:
                import json
                raw = critic_resp.content.strip()
                if raw.startswith("```"):
                    parts = raw.split("```")
                    if len(parts) >= 3:
                        raw = parts[1]
                        if raw.startswith("json"):
                            raw = raw[4:]
                crit_data = json.loads(raw.strip())

                step.confidence = float(crit_data.get("confidence", 1.0))
                step.critique = crit_data.get("critique", "none")
                step.correction = crit_data.get("correction", "none")

                issue_found = crit_data.get("issue_found", False)
                if issue_found and step.confidence < self.threshold:
                    # Inject correction into CoT context for next step
                    if step.correction and step.correction != "none":
                        context_additions.append(f"In Step {step_num}: {step.correction}")
                        corrections += 1
                        step.correction = step.correction  # mark as applied

            except Exception:
                step.confidence = 1.0

            steps.append(step)

            # Check if CoT has reached final answer
            if "final answer" in step_content.lower():
                break

        # Extract final answer
        final_answer = ""
        for step in reversed(steps):
            if "final answer" in step.content.lower():
                lines = step.content.split("\n")
                for line in lines:
                    if "final answer" in line.lower():
                        final_answer = line.split(":", 1)[-1].strip()
                        break
                break

        if not final_answer:
            final_answer = steps[-1].content if steps else "No conclusion reached."

        # Build annotated reasoning trace
        trace_lines = [f"=== ParallelThink Reasoning Trace ===", f"Question: {question}", ""]
        for s in steps:
            trace_lines.append(f"Step {s.step_num}: {s.content}")
            if s.critique and s.critique != "none":
                trace_lines.append(f"  [CRITIC confidence={s.confidence:.2f}]: {s.critique}")
            if s.correction and s.correction != "none" and s.confidence < self.threshold:
                trace_lines.append(f"  [CORRECTION APPLIED]: {s.correction}")
            trace_lines.append("")

        trace_lines.append(f"Final Answer: {final_answer}")
        trace_lines.append(f"Corrections applied: {corrections}/{len(steps)}")

        return ThinkResult(
            question=question,
            final_answer=final_answer,
            reasoning_trace="\n".join(trace_lines),
            steps=steps,
            corrections=corrections,
            total_steps=len(steps),
        )
