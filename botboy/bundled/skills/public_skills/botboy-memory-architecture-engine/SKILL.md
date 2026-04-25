---
name: botboy-memory-architecture-engine
description: Design, review, and evolve BotBoy memory systems across short-term context, durable storage, retrieval, and reflection loops. Use when improving BotBoy's memory model, comparing memory backends or retrieval strategies, adding memory-aware evals, or deciding how conversation, history, semantic recall, and reflection should cooperate instead of drifting apart.
---

# Quick Start

Think in layers, not just in storage.

- Separate working context, history, and semantic recall.
- Decide what should be remembered, for how long, and for what downstream behavior.
- Keep retrieval explainable and testable.
- Tie every memory change back to a user-visible gain or operational guarantee.

# Workflow

Follow this order:

1. Read `references/memory-layers.md`.
2. Identify which memory problem is actually being solved.
3. Map the problem to storage, retrieval, retention, or reflection.
4. Change the narrowest memory layer that can solve it.
5. Add eval or regression evidence for the new behavior.

# Memory Rules

Prefer:

- explicit retention logic over accidental persistence
- retrieval quality over raw volume
- memory paths that preserve auditability
- evaluation against realistic recall tasks

# Resources

- Read `references/memory-layers.md` before making architectural decisions.
- Read `references/retrieval-tradeoffs.md` when comparing recall strategies.
- Run `scripts/memory_path_map.py <repo-root>` to enumerate current BotBoy memory-related modules.
