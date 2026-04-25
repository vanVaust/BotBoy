---
name: botboy-communication-doctrine
description: Design BotBoy communication, handoff, status, and coordination language for humans and agents, including concise updates, escalation notes, audience-aware briefings, and execution-ready summaries. Use when writing BotBoy status messages, agent handoffs, incident summaries, or user-facing progress notes that must stay crisp and operational.
---

# Quick Start

- State the objective first.
- Separate facts from inference.
- Keep the message execution-ready.
- Match the audience without losing precision.

# Workflow

1. Read `references/briefing-patterns.md`.
2. Read `references/handoff-format.md`.
3. Identify the audience and the action expected next.
4. Compress the message into a clear operational brief.
5. Preserve traceability to the underlying work or decision.

# Rules

- Prefer one clear next step over a long narrative.
- Use plain language for humans, structured language for agents.
- Escalate clearly when risk or uncertainty matters.

# Resources

- Read `references/briefing-patterns.md` for the core message shapes.
- Read `references/handoff-format.md` to produce crisp, reusable handoffs.
- Run `scripts/handoff_brief.py` to generate a structured brief from key fields.
