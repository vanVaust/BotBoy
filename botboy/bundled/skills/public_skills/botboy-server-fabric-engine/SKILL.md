---
name: botboy-server-fabric-engine
description: Design and analyze BotBoy server fabric across CLI, stdlib gateway, FastAPI gateway, MCP, web UI, and supporting service surfaces. Use when mapping routes, decomposing transport boundaries, reviewing server topology, or planning infrastructure changes that affect BotBoy's exposed surfaces.
---

# Quick Start

Work from the service surface map.

- Identify the current transport and entry point.
- Map the affected server, bridge, or UI surface.
- Separate shared orchestration from transport-specific logic.
- Keep route and contract changes aligned across the fabric.

# Workflow

Follow this order:

1. Read `references/surface-map.md`.
2. Identify the service surface being changed.
3. Check which files own the transport boundary.
4. Update the narrowest boundary first.
5. Verify the public shape stays coherent across the fabric.

# BotBoy Focus

Use this skill for:

- gateway route design
- endpoint and transport topology
- stdlib and FastAPI parity
- MCP bridge shape
- dashboard and UI backplane alignment

# Resources

- Read `references/surface-map.md` before changing any server boundary.
- Run `scripts/summarize_surface.py <repo-root>` to emit a compact JSON surface map.
