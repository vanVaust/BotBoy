# Project Targets

Use these repo anchors when a skill must align to the current BotBoy architecture:

- `botboy/skills/manager.py`: contract parsing, discovery behavior, and skill metadata expectations.
- `botboy/skills/runtime.py`: runtime tier and approval behavior.
- `botboy/__main__.py`: orchestration, command routing, traces, metrics, and eval entry points.
- `botboy_mcp_server.py`: MCP bridge tools and exported contract metadata.
- `tests/test_skill_contracts.py`: contract regression behavior.
- `tests/test_runtime_policy.py`: runtime and approval regression behavior.
- `tests/test_eval_runner.py`: eval baseline verification.
- `tests/test_dashboard_payload.py`: Control Center payload expectations.

Use the canonical workspace for BotBoy work:

- `BAU-botboI-mitClaude/phase2_extracted/botboy_v3_dev`

Treat `examples/skills/` as product-facing BotBoy sample skills.
Treat `skills/` as agent-facing operational skills unless the user asks to merge the two worlds.
