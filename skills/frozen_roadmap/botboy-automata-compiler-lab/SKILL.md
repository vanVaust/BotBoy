---
name: botboy-automata-compiler-lab
description: Design and analyze automata, state machines, parsers, and compiler-like workflows for BotBoy command routing and formal task modeling. Use when BotBoy needs a state machine, parser, grammar, transition model, or compiler-style transformation that should be explicit, testable, and structurally sound.
---

# Quick Start

Think in states, transitions, and invariants.

- Define the state space first.
- Define valid transitions second.
- Define error and rejection states explicitly.
- Keep transformations small enough to verify.

# Workflow

Follow this order:

1. Read `references/automata-principles.md`.
2. Identify the model: parser, state machine, or compiler transform.
3. Write down states, inputs, outputs, and invariants.
4. Build the smallest machine that expresses the desired behavior.
5. Add a check or report that proves the machine is consistent.

# Rules

Prefer:

- explicit transitions over implicit branching
- small grammars over magical parsing
- invariants before optimization
- deterministic output for the same input model

# Resources

- Read `references/automata-principles.md` before designing the machine.
- Read `references/compiler-checklist.md` when the workflow transforms one representation into another.
- Run `scripts/state_machine_summary.py <json-model>` to summarize a state machine model.
