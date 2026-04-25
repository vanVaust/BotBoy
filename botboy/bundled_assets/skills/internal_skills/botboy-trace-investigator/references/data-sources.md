# Data Sources

Use these BotBoy evidence sources together:

- trace summary and trace detail from TraceStore-backed APIs
- history records keyed by `request_id` and `principal`
- metrics summaries for command shape and recent command behavior
- monitoring payloads for timing and health context
- dashboard payload for a stitched operational view

Look for correlation points:

1. `request_id`
2. `principal`
3. command text or route
4. span status
5. latency or timing anomalies
