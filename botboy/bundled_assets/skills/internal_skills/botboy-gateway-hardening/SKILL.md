---
name: botboy-gateway-hardening
description: Harden and verify BotBoy gateway behavior across stdlib and FastAPI modes, including login, refresh, API keys, principal management, authorization, rate limiting, and contract parity. Use when adding or debugging protected routes, reproducing auth or rate-limit edge cases, tightening transport semantics, or expanding end-to-end regression coverage for both BotBoy server implementations.
---

# Quick Start

Treat the two gateway implementations as one product surface with two transports.

- Verify the intended behavior first.
- Reproduce in both stdlib and FastAPI paths when relevant.
- Prefer tests that assert user-visible auth and rate-limit semantics.
- Record any parity gap explicitly.

# Workflow

Follow this order:

1. Read `references/auth-hardening-checklist.md`.
2. Identify whether the issue is auth, principal management, rate limit, route protection, or parity.
3. Inspect both gateway implementations before fixing only one.
4. Add or tighten regressions close to the failing transport path.
5. Re-run the narrow tests first, then the broader suite if the change touches shared logic.

# High-Value Targets

Prioritize:

- anonymous access to protected routes
- incorrect login or refresh semantics
- inconsistent principal propagation
- missing or mismatched rate-limit behavior
- MCP or dashboard-facing contract drift caused by gateway changes

# Resources

- Read `references/auth-hardening-checklist.md` for the main verification path.
- Read `references/parity-matrix.md` when a change should match stdlib and FastAPI behavior.
- Run `scripts/route_matrix.py` to list the discovered FastAPI route decorators in the current server file.
