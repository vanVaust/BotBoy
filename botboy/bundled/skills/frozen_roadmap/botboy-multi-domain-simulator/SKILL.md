---
name: botboy-multi-domain-simulator
description: Design safe multi-domain simulations for BotBoy across agents, workflows, incidents, decision paths, and system interactions without pretending to control the physical world. Use when a feature or policy should be explored through simulation first, when BotBoy needs scenario-based stress testing, when multiple interacting components must be modeled before implementation, or when Codex should convert a broad system question into a bounded simulated environment.
---

# Quick Start

Simulate the decision environment, not fantasy omnipotence.

- Define the domain and the simulation boundary.
- State the variables, actors, and signals explicitly.
- Keep the simulator bounded and inspectable.
- Use simulations to learn, compare, and de-risk design choices.

# Workflow

Follow this order:

1. Read `references/simulation-frame.md`.
2. Define the scenario, actors, state, and transitions.
3. Decide what BotBoy should observe and what it may change.
4. Make success criteria explicit.
5. Use the simulation to generate design insight, not false certainty.

# Simulation Rules

Prefer:

- bounded scenarios
- explicit state transitions
- safe abstractions over real-world operational claims
- outputs that inform architecture, evals, or policies

# Resources

- Read `references/simulation-frame.md` before modeling a scenario.
- Read `references/safe-scope.md` to keep the simulation bounded and non-harmful.
- Run `scripts/scenario_template.py --name <scenario>` to emit a starter simulation skeleton as JSON.
