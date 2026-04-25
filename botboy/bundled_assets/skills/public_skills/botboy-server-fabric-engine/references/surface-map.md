# Surface Map

Canonical BotBoy surfaces:

- `botboy/__main__.py`: CLI orchestrator and command routing
- `botboy/gateway/server.py`: FastAPI gateway
- `botboy/gateway/simple_server.py`: stdlib gateway
- `botboy_mcp_server.py`: MCP tool bridge
- `web/index.html`: canonical control center UI
- `web/dashboard.html`: prototype UI

Change rule:

1. update the owning boundary
2. keep shared semantics aligned
3. preserve transport-specific differences only when intentional
