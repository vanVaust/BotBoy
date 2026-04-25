---
name: botboy-virtual-dev-range
description: Create controlled virtual proving grounds for risky BotBoy features, integration ideas, and behavior changes before they touch the main product path. Use when a change is too uncertain for direct implementation, when a feature needs a safe experiment harness, when multiple implementation strategies should be trialed in isolation, or when Codex should stage work in a temporary digital range instead of destabilizing the main runtime.
---

# Quick Start

Treat the range as a staging ground for learning.

- Isolate the risky idea from the main runtime.
- Define what the range is allowed to simulate and what it is not.
- Keep experiments disposable and reproducible.
- Promote only what proves its value.

# Workflow

Follow this order:

1. Read `references/range-principles.md`.
2. Define the risky feature or hypothesis.
3. Decide the minimum environment needed to test it.
4. Record what success, failure, and rollback look like.
5. Keep the range outputs structured enough to compare approaches.

# Range Rules

Prefer:

- cheap experiments
- explicit isolation boundaries
- one hypothesis per range run
- promotion criteria before implementation

# Resources

- Read `references/range-principles.md` before creating an experiment path.
- Read `references/promotion-criteria.md` when deciding whether a range result should graduate into BotBoy proper.
- Run `scripts/range_manifest.py --name <experiment>` to create a minimal experiment manifest skeleton.
