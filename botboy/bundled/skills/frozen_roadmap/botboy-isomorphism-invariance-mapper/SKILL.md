---
name: botboy-isomorphism-invariance-mapper
description: Analyze structural equivalence, invariants, and shape-preserving transformations across BotBoy modules, flows, and data contracts. Use when different BotBoy subsystems appear to solve the same problem in different forms, when a refactor should preserve behavior under transformation, when contract drift must be detected through invariant reasoning, or when Codex needs structure-aware comparison instead of line-by-line diffing.
---

# Quick Start

Look for preserved structure, not surface similarity.

- Start from the behavior or contract that must remain stable.
- Identify what can change and what must stay invariant.
- Compare shapes, roles, and relations before comparing spelling.
- Use invariants to drive refactors and parity checks.

# Workflow

Follow this order:

1. Read `references/invariant-categories.md`.
2. Identify the structures or flows being compared.
3. State the invariant in operational language.
4. Map the transformation that should preserve it.
5. Use the result to guide refactor, parity review, or drift detection.

# Design Rules

Prefer:

- invariants that are testable
- comparisons at contract and flow level
- preserved roles over preserved syntax
- explicit statements of what is allowed to vary

# Resources

- Read `references/invariant-categories.md` before reasoning about structural equivalence.
- Read `references/transformation-patterns.md` when a refactor should preserve behavior.
- Run `scripts/invariant_surface_map.py <repo-root>` to identify likely invariant-bearing modules in BotBoy.
