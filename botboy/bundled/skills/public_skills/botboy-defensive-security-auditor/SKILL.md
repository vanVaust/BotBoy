---
name: botboy-defensive-security-auditor
description: Defensively audit BotBoy code, gateway, skill, and config surfaces for auth, approval, sandbox, secret, rate limit, and path traversal risks. Use when hardening BotBoy behavior, reviewing a risky change, checking fail-closed policy, or adding regressions for security-sensitive paths.
---

# Quick Start

Start from the risk surface, not the symptom.

- Identify the entry point.
- Classify the risk as auth, secret, approval, sandbox, filesystem, or transport.
- Verify the behavior fails closed.
- Add or update the narrowest regression that proves the fix.

# Workflow

Follow this order:

1. Read `references/security-checklist.md`.
2. Inspect the code path and its trust boundary.
3. Confirm the expected denial or containment behavior.
4. Record the smallest reproducible risk case.
5. Add a regression close to the failing path.

# BotBoy Focus

Prioritize these surfaces:

- `botboy/gateway/server.py`
- `botboy/gateway/simple_server.py`
- `botboy/skills/runtime.py`
- `botboy/skills/manager.py`
- `botboy_mcp_server.py`
- trace, history, and rate-limit behavior

# Resources

- Read `references/security-checklist.md` before making changes.
- Run `scripts/scan_risk_patterns.py <path>` to flag risky code patterns in a file or tree.
