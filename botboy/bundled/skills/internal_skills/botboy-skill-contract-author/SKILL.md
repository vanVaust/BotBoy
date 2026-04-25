---
name: botboy-skill-contract-author
description: Create, update, review, and validate BotBoy skill folders with canonical SKILL.md metadata, agent UI metadata, helper resources, and BotBoy-aligned contract intent. Use when creating a new BotBoy skill, migrating an older skill folder, tightening skill descriptions and triggers, adding references or scripts, or keeping skill artifacts consistent across SKILL.md, agents/openai.yaml, and BotBoy's contract-driven architecture.
---

# Quick Start

Create or revise the skill folder first.

- Write `SKILL.md` with trigger-strong metadata and a short imperative body.
- Keep the body lean. Put detailed procedures, checklists, and examples in `references/`.
- Put deterministic or repeated helper logic in `scripts/`.
- Create `agents/openai.yaml` with `display_name`, `short_description`, and a `default_prompt` that explicitly mentions `$botboy-skill-contract-author`.

# Workflow

Follow this order:

1. Identify the repeated BotBoy task the skill should standardize.
2. Decide whether the skill serves Codex agents, BotBoy runtime work, or both.
3. Write a single-sentence description that names the action and the trigger contexts.
4. Add only the resources that remove repeated work.
5. Check the result against the contract checklist in `references/contract-checklist.md`.
6. Run `scripts/scan_skill_contracts.py` on the surrounding skill library to catch drift.

# BotBoy Alignment

Preserve a clean separation between:

- trigger metadata in `SKILL.md`
- UI-facing invocation metadata in `agents/openai.yaml`
- detailed operating knowledge in `references/`
- repeatable helper code in `scripts/`

If the user wants a runtime-executable BotBoy skill, map the skill intent back onto the BotBoy contract model in `references/project-targets.md` and update the relevant test coverage.

# Resources

- Read `references/contract-checklist.md` before editing.
- Read `references/project-targets.md` when the skill must touch BotBoy parser, runtime, or test paths.
- Run `scripts/scan_skill_contracts.py <path>` to audit a skill collection for missing core artifacts.
