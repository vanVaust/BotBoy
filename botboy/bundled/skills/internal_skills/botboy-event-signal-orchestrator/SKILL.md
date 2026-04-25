---
name: botboy-event-signal-orchestrator
description: Design and organize BotBoy event triggers, signals, activation rules, and deferred follow-up pathways across commands, automations, traces, and operational surfaces. Use when BotBoy needs clearer event-driven behavior, when a signal should activate or suppress a skill, when multiple trigger sources must be synchronized, or when the system needs a cleaner event-to-action routing model.
---

# Quick Start

Treat events as contracts between system state and action.

- Identify the signal source first.
- Define what should happen, what should not happen, and when.
- Keep trigger rules observable and debuggable.
- Avoid event logic that only exists implicitly in scattered code.

# Workflow

Follow this order:

1. Read `references/event-taxonomy.md`.
2. Identify the signal source and the affected action surface.
3. Decide whether the event should trigger, gate, defer, or annotate behavior.
4. Prefer a small explicit mapping over hidden condition sprawl.
5. Connect the new rule to a validation surface.

# Signal Rules

Prefer:

- explicit trigger conditions
- stable event naming
- one mapping layer for event-to-action intent
- traceability for every nontrivial signal

# Resources

- Read `references/event-taxonomy.md` before changing trigger logic.
- Read `references/activation-rules.md` to choose between trigger, gate, defer, or annotate.
- Run `scripts/signal_surface_map.py <repo-root>` to inventory BotBoy files that likely host event or signal logic.
