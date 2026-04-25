---
name: botboy-runtime-policy-auditor
description: Audit BotBoy runtime policy and security behavior for trusted and untrusted skill execution, including runtime tier resolution, approval gates, network and filesystem permissions, and fail-closed defaults. Use when reviewing or hardening BotBoy skill execution, debugging why a skill received the wrong runtime, explaining approval behavior, or adding regression coverage around risky execution paths.
---

# Quick Start

Start from the contract and the observed behavior.

- Identify whether the skill or command path is trusted.
- Identify whether it needs network or filesystem access.
- Compare expected runtime and approval behavior against the current policy.
- Add or update regressions after the policy is clarified.

# Workflow

Follow this order:

1. Read `references/policy-matrix.md`.
2. Reconstruct the expected decision from the skill's trust and capability profile.
3. Check the active implementation path in `botboy/skills/runtime.py`.
4. Verify the command enters the policy with the right metadata.
5. Record gaps as testable failure modes, not just prose observations.

# What Good Looks Like

Prefer these outcomes:

- untrusted code never drifts into in-process execution
- risky capabilities force approval or stronger isolation
- missing infrastructure fails closed instead of silently downgrading
- tests describe the policy in terms of user-visible risk

# Resources

- Read `references/policy-matrix.md` for the expected decision rules.
- Read `references/failure-modes.md` before writing new tests or fixes.
- Run `scripts/runtime_policy_matrix.py` to print a policy decision for a specific risk profile.
