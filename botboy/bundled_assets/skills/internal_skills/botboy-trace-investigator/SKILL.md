---
name: botboy-trace-investigator
description: Investigate BotBoy runs by correlating traces, request IDs, principals, history, monitoring, and metrics into one execution narrative. Use when debugging a failing BotBoy command path, analyzing latency or approval behavior, explaining an incident from TraceStore data, or writing an audit-friendly summary from request, principal, and trace evidence.
---

# Quick Start

Begin from the narrowest stable identifier available.

- Prefer `request_id` or run ID when available.
- Fall back to principal plus time window if the run ID is unknown.
- Correlate traces with history and metrics before proposing a root cause.
- Report both the symptom path and the control-path context, such as approval or auth behavior.

# Workflow

Follow this order:

1. Read `references/data-sources.md`.
2. Collect the trace summary or run detail.
3. Pull matching history, monitoring, and metrics context.
4. Build a short step-by-step execution narrative.
5. Separate hard evidence from inference.

# Investigation Rules

Prefer:

- one coherent timeline over a pile of raw artifacts
- request-scoped analysis before global speculation
- status transitions and latency spikes over generic "something failed" summaries
- explicit notes when a conclusion is inferred rather than directly observed

# Resources

- Read `references/data-sources.md` to know which BotBoy surfaces carry correlated evidence.
- Read `references/investigation-checklist.md` before writing an incident summary.
- Run `scripts/trace_report.py <json-path>` to summarize a saved trace or dashboard artifact.
