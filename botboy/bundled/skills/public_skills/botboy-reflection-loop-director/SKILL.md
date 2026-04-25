---
name: botboy-reflection-loop-director
description: Design, tighten, and evaluate BotBoy reflection loops for self-critique, plan revision, and post-run improvement without losing auditability or operator control. Use when BotBoy needs better reflection prompts, improved critique stages, safer self-correction behavior, or clearer reasoning-loop integration across planning, evals, traces, and command execution.
---

# Quick Start

Treat reflection as a controlled feedback loop, not as freeform rumination.

- Start from the concrete failure or quality gap.
- Decide where reflection should intervene: before execution, after execution, or during review.
- Keep reflection observable through traces, history, or eval artifacts.
- Prefer smaller sharper loops over sprawling self-analysis.

# Workflow

Follow this order:

1. Read `references/reflection-loop-patterns.md`.
2. Identify the exact failure mode the loop should improve.
3. Choose the narrowest insertion point in BotBoy's reasoning flow.
4. Define the expected output of reflection in operational terms.
5. Add or plan validation through evals, traces, or targeted regression tests.

# Design Rules

Prefer:

- reflection that changes a decision surface
- explicit loop boundaries
- critiques that are actionable and testable
- loops that preserve auditability

# Resources

- Read `references/reflection-loop-patterns.md` before reshaping any loop.
- Read `references/validation-hooks.md` when deciding how to verify a reflection change.
- Run `scripts/reflection_touchpoints.py <repo-root>` to locate reflection-related files in BotBoy.
