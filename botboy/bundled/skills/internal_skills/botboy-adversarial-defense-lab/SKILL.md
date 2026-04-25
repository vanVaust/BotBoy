---
name: botboy-adversarial-defense-lab
description: Build defensive adversarial testing, hardening, and misuse-resistance workflows for BotBoy code, gateways, skills, and prompts. Use when BotBoy needs to probe weak points safely, design defensive countermeasures, add abuse-case regressions, or review a surface for resilience without creating offensive capability.
---

# Quick Start

Treat adversarial work as defense engineering.

- Identify the surface to harden.
- Enumerate likely abuse patterns and failure modes.
- Convert each risky pattern into a testable defensive check.
- Keep the output focused on resilience, detection, and containment.

# Workflow

Follow this order:

1. Read `references/defense-checklist.md`.
2. Identify the BotBoy surface and the plausible abuse patterns.
3. Prefer the smallest reproducible probe.
4. Turn each finding into a mitigation or regression test.
5. Keep the result defensive and audit-friendly.

# Rules

Prefer:

- misuse resistance over cleverness
- detection and containment over exploitation
- explicit abuse-case tests over vague warnings
- safe simulation over live attack behavior

# Resources

- Read `references/defense-checklist.md` before changing anything risky.
- Read `references/risk-patterns.md` when scanning a new surface.
- Run `scripts/scan_risk_patterns.py <path>` to summarize suspect patterns in a code file or repository tree.
