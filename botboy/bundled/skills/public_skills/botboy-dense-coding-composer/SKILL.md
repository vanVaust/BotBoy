---
name: botboy-dense-coding-composer
description: Turn large BotBoy engineering goals into compact, high-yield implementation moves with strong leverage, clear boundaries, and minimal wasted edits. Use when a change touches many concepts but should still be executed as a small set of sharp code changes, when a feature must be decomposed into dense deliverable slices, or when Codex needs a senior-engineer style implementation strategy instead of broad churn.
---

# Quick Start

Optimize for leverage, not line count.

- Find the control points first.
- Shrink the change until each edit has disproportionate impact.
- Preserve observability and tests while compressing the implementation.
- Avoid broad edits that only feel productive.

# Workflow

Follow this order:

1. Read `references/leverage-patterns.md`.
2. Identify the two or three files that actually control the behavior.
3. Choose the smallest change surface that can move the system safely.
4. Pair every dense edit with the tightest useful verification.
5. Explain the architecture move, not only the diff.

# Dense Coding Rules

Prefer:

- choke points over scattered patches
- adapters and shared helpers over repeated local fixes
- one strong test over five weak ones
- explicit invariants over implicit luck

# Resources

- Read `references/leverage-patterns.md` before decomposing a change.
- Read `references/change-shaping.md` when an ask feels too large or vague.
- Run `scripts/find_control_points.py <repo-root> <term>` to surface files with concentrated relevance.
