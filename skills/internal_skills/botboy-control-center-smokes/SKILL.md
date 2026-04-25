---
name: botboy-control-center-smokes
description: Validate the BotBoy Control Center UI against dashboard payloads with browser-style smoke checks, payload contract assertions, and readability-focused regressions. Use when verifying the canonical operations UI, catching broken cards or missing payload fields, testing visual stability after dashboard changes, or keeping web/index.html aligned with dashboard, monitoring, and trace APIs.
---

# Quick Start

Start from the contract between payload and UI.

- Confirm which payload fields the UI expects.
- Confirm which HTML markers or render branches expose those fields.
- Add the smallest smoke that proves the UI still expresses the operational truth.
- Prefer lightweight contract checks before expensive visual work.

# Workflow

Follow this order:

1. Read `references/ui-contract.md`.
2. Read `references/smoke-plan.md`.
3. Check the payload shape first.
4. Check HTML markers and render paths second.
5. Add visual or browser checks only after the contract path is stable.

# Smoke Strategy

Prefer this ladder:

1. payload contract assertion
2. HTML marker assertion
3. render-function regression
4. browser or screenshot smoke

Keep the smoke suite aimed at breakage detection, not cosmetic perfection.

# Resources

- Read `references/ui-contract.md` for the most important payload-to-UI bindings.
- Read `references/smoke-plan.md` before adding a heavier browser-level check.
- Run `scripts/check_dashboard_contract.py <payload.json>` to validate that a saved dashboard payload exposes the core fields the Control Center expects.
