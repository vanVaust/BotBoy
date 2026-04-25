---
name: botboy-omni-skill-architect
description: Design, categorize, phase, and evolve large BotBoy skill libraries into a coherent high-performance catalog with safe boundaries, build waves, and invocation rules. Use when Codex needs to add many new BotBoy or agent skills at once, cluster broad user categories into a maintainable taxonomy, decide which skill should be built now versus later, or keep a growing skill portfolio aligned with BotBoy's architecture and safety constraints.
---

# Quick Start

Start from the catalog, not from random skill sprawl.

- Group related asks into stable categories.
- Replace unsafe or disallowed categories with defensive or simulated equivalents.
- Assign each skill a build phase and a clear invocation boundary.
- Keep the catalog machine-readable and human-readable at the same time.

# Workflow

Follow this order:

1. Read `references/catalog-usage.md`.
2. Read `skill-catalog/BOTBOY_OMNI_SKILL_CATALOG.md`.
3. Decide whether the ask needs a new skill, a new category, or just a better invocation rule.
4. Update the JSON catalog first.
5. Update the human catalog and the integration playbook second.
6. Build real skill folders only for the highest-value items in the current phase.

# Design Rules

Prefer:

- one strong skill over three overlapping weak skills
- phased rollout over mass creation of empty shells
- safe substitutions for harmful categories
- explicit trigger guidance over vague "general intelligence" claims

# Resources

- Read `references/catalog-usage.md` before editing the catalog.
- Read `skill-catalog/SKILL_INTEGRATION_PLAYBOOK.md` when deciding build order.
- Run `scripts/select_skill_phase.py` to filter the catalog by phase, category, or query.
