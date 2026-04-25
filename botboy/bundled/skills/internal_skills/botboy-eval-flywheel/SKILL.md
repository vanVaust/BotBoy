---
name: botboy-eval-flywheel
description: Create, extend, run, and summarize BotBoy eval and replay baselines from manifests, replay seeds, reports, and regression findings. Use when turning a bug or feature into a durable eval case, growing replay coverage, generating or reviewing JSON report artifacts, or tightening the reproducible quality loop around BotBoy's eval runner.
---

# Quick Start

Treat every real failure as a candidate eval.

- Add or update the manifest case.
- Add replay seed data if the failure depends on a request sequence or identity.
- Run the eval path and capture a report artifact.
- Keep the new case small, reproducible, and tied to a real product risk.

# Workflow

Follow this order:

1. Read `references/eval-loop.md`.
2. Decide whether the gap is a new case, a replay seed, or a report-diff problem.
3. Edit the manifest or seed with the smallest reproducible scenario.
4. Run the eval baseline.
5. Summarize the result in terms of behavior, not only pass counts.

# Design Rules

Prefer:

- a small case over a broad synthetic one
- replay seeds for auth, rate-limit, and ordering problems
- report artifacts when the user needs evidence or comparison
- new eval coverage whenever a regression was fixed in product code

# Resources

- Read `references/eval-loop.md` for the operating model.
- Read `references/reporting-guidelines.md` before changing report output or failure summaries.
- Run `scripts/new_eval_case.py` to append a starter case to an eval manifest.
