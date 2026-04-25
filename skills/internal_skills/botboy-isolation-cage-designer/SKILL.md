---
name: botboy-isolation-cage-designer
description: Design and harden BotBoy containment, sandbox, and isolation envelopes for untrusted or high-risk work, including runtime tiers, filesystem and network boundaries, fail-closed behavior, and execution safety checks. Use when improving sandbox policy, selecting runtime containment, or designing a safer execution cage for a BotBoy skill or agent.
---

# Quick Start

- Classify the risk before choosing a runtime.
- Minimize the allowed surface area.
- Prefer fail-closed behavior over fallback convenience.
- Tie every isolation decision to a testable policy rule.

# Workflow

1. Read `references/isolation-patterns.md`.
2. Read `references/runtime-boundaries.md`.
3. Map the request to trust, network, filesystem, and execution needs.
4. Pick the narrowest safe cage.
5. Add a regression for the intended failure mode.

# Rules

- Never silently widen an unsafe runtime.
- Keep containment decisions explicit in code and tests.
- Treat missing isolation support as a policy event, not a soft warning.

# Resources

- Read `references/isolation-patterns.md` for the design checklist.
- Read `references/runtime-boundaries.md` to map isolation onto BotBoy modules.
- Run `scripts/isolation_profile.py` to summarize a policy decision for a given risk profile.
