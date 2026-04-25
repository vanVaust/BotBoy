---
name: botboy-formal-logic-engine
description: Reason about BotBoy invariants, implications, contradictions, and rule sets for contracts, policies, evals, and architecture decisions. Use when verifying expectations, checking policy consistency, comparing requirements, or reducing BotBoy behavior to explicit facts and rules.
---

# Quick Start

Write the facts down first.

- Separate facts from rules.
- Check implications before conclusions.
- Surface contradictions explicitly.
- Keep the result short enough to act on.

# Workflow

Follow this order:

1. Read `references/invariant-patterns.md`.
2. Extract the facts and rules from the BotBoy problem.
3. Check which implications are satisfied.
4. Mark contradictions or missing premises.
5. Summarize the smallest valid conclusion set.

# BotBoy Focus

Use this skill for:

- contract and policy checks
- eval expectation validation
- routing and runtime rule consistency
- architecture invariants
- decision trees and traceable reasoning

# Resources

- Read `references/invariant-patterns.md` before formalizing a problem.
- Run `scripts/check_invariants.py <json-file>` to verify a simple fact-and-rule model.
