---
name: botboy-skill-evolution-forge
description: Evolve, merge, split, and refactor BotBoy skill libraries as they grow, while preserving clear triggers, low-overlap taxonomy, and reusable resources. Use when multiple BotBoy skills start overlapping, when a broad skill must be split into focused descendants, when several adjacent skills should be merged, or when a skill library needs a disciplined versioned redesign instead of ad hoc sprawl.
---

# Quick Start

Treat skill growth as architecture work, not cleanup.

- Identify overlap before writing new folders.
- Split broad skills only when the triggers or resources diverge.
- Merge skills only when the user-facing invocation stays clear.
- Preserve the strongest name and trigger surface whenever possible.

# Workflow

Follow this order:

1. Read `references/evolution-rules.md`.
2. Read the current catalog and neighboring skills.
3. Decide whether the right move is merge, split, rename, or retire.
4. Update the taxonomy before touching resource files.
5. Keep each resulting skill simpler than the one it replaced.

# Evolution Rules

Prefer:

- focused verbs over abstract labels
- one trigger surface per job
- resources that clearly belong to one skill
- deprecation or migration notes in references instead of bloated frontmatter

# Resources

- Read `references/evolution-rules.md` before changing a skill family.
- Read `references/overlap-signals.md` to decide when a split or merge is warranted.
- Run `scripts/skill_overlap_report.py <skills-dir>` to flag likely naming or description collisions.
