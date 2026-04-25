"""ReflectionEngine — Horizont-Wächter / inner-dialogue adversarial refinement."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional


@dataclass
class ReflectionResult:
    original: str
    final: str
    refined: bool
    rounds: int
    confidence: float
    skipped: bool = False
    skip_reason: str = ""


_WATCHER_SYSTEM = """You are the Horizont-Wächter (Horizon Watcher) — a critical evaluator.
Your role: evaluate AI responses for accuracy, completeness, and helpfulness.

Given an original user query and a proposed response, evaluate:
1. Is the response accurate?
2. Is it complete (doesn't miss key aspects)?
3. Is it appropriately concise?
4. Does it directly address the user's intent?

Respond ONLY as JSON:
{"approved": true/false, "confidence": 0.0-1.0, "critique": "...", "suggestion": "..."}

Be strict but fair. Approve if confidence >= threshold."""

_THINKER_REFINE_SYSTEM = """You are BotBoy, a helpful AI agent.
You previously gave a response that was critiqued. Apply the critique and provide an improved response.
Be concise and accurate. Do NOT mention the critique or refinement process in your response."""


class ReflectionEngine:
    """
    Two-phase inner-dialogue refinement:
      Thinker  → generates primary response
      Watcher  → evaluates and signals if refinement is needed
      Thinker  → refines if confidence < threshold (up to max_rounds)

    If llm is None or enabled=False: skips and returns original immediately.
    Skips short/trivial commands (< 3 words and < 100 chars).
    """

    def __init__(
        self,
        llm=None,
        max_rounds: int = 2,
        threshold: float = 0.75,
        enabled: bool = True,
    ) -> None:
        self._llm = llm
        self.max_rounds = max_rounds
        self.threshold = threshold
        self.enabled = enabled

    def _should_skip(self, command: str) -> tuple[bool, str]:
        if not self.enabled:
            return True, "disabled"
        if self._llm is None:
            return True, "no_llm"
        words = command.strip().split()
        if len(words) < 3 and len(command) < 100:
            return True, "trivial_command"
        return False, ""

    async def reflect(self, command: str, initial_response: str) -> ReflectionResult:
        should_skip, reason = self._should_skip(command)
        if should_skip:
            return ReflectionResult(
                original=initial_response,
                final=initial_response,
                refined=False,
                rounds=0,
                confidence=1.0,
                skipped=True,
                skip_reason=reason,
            )

        current_response = initial_response
        rounds = 0
        confidence = 0.0

        for _ in range(self.max_rounds):
            # Watcher evaluates
            eval_prompt = (
                f"User query: {command}\n\nProposed response:\n{current_response}\n\n"
                f"Evaluate this response. Confidence threshold is {self.threshold}."
            )

            try:
                import json
                watcher_llm = _make_watcher_llm(self._llm, _WATCHER_SYSTEM)
                watcher_resp = await watcher_llm.chat(eval_prompt, use_history=False)

                # Parse JSON evaluation
                raw = watcher_resp.content.strip()
                # Strip markdown fences if present
                if raw.startswith("```"):
                    raw = raw.split("```")[1]
                    if raw.startswith("json"):
                        raw = raw[4:]

                eval_data = json.loads(raw)
                approved = eval_data.get("approved", True)
                confidence = float(eval_data.get("confidence", 0.8))
                critique = eval_data.get("critique", "")
                suggestion = eval_data.get("suggestion", "")

                rounds += 1

                if approved or confidence >= self.threshold:
                    break

                # Thinker refines based on critique
                refine_prompt = (
                    f"Original query: {command}\n"
                    f"Your previous response: {current_response}\n"
                    f"Critique: {critique}\n"
                    f"Suggestion: {suggestion}\n\n"
                    "Provide an improved response:"
                )
                thinker_llm = _make_thinker_llm(self._llm, _THINKER_REFINE_SYSTEM)
                refined_resp = await thinker_llm.chat(refine_prompt, use_history=False)

                if refined_resp.success and refined_resp.content.strip():
                    current_response = refined_resp.content.strip()

            except Exception:
                # Any error → accept current response
                confidence = 0.8
                break

        return ReflectionResult(
            original=initial_response,
            final=current_response,
            refined=(current_response != initial_response),
            rounds=rounds,
            confidence=confidence,
        )


def _make_watcher_llm(base_llm, system_prompt: str):
    """Create a watcher LLM instance with a different system prompt."""
    from botboy.llm.integration import LLMIntegration
    return LLMIntegration(backend=base_llm._backend, system_prompt=system_prompt)


def _make_thinker_llm(base_llm, system_prompt: str):
    """Create a thinker LLM instance with a refinement system prompt."""
    from botboy.llm.integration import LLMIntegration
    return LLMIntegration(backend=base_llm._backend, system_prompt=system_prompt)
