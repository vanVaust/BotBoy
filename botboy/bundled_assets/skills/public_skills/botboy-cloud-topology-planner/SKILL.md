---
name: botboy-cloud-topology-planner
description: Plan BotBoy cloud topology, deployment shape, service boundaries, and reliability tradeoffs without losing the local-first architecture or operational observability. Use when deciding how BotBoy should expand into cloud-backed environments, when comparing deployment shapes, when mapping gateways and services onto infrastructure tiers, or when a cloud rollout needs strong architecture rather than generic platform advice.
---

# Quick Start

Treat cloud as an extension of BotBoy's architecture, not a replacement for it.

- Start from the existing local-first control surfaces.
- Decide which components must stay local and which may become remote.
- Preserve traceability, auth semantics, and evalability across the topology.
- Prefer simple deployable slices over platform sprawl.

# Workflow

Follow this order:

1. Read `references/topology-principles.md`.
2. Identify the BotBoy components in scope.
3. Choose the minimal viable deployment shape.
4. Evaluate reliability, security, and observability tradeoffs.
5. Produce a topology recommendation tied to real BotBoy modules.

# Planning Rules

Prefer:

- explicit service boundaries
- one deployment reason per component
- observability-preserving designs
- rollback and simplification paths

# Resources

- Read `references/topology-principles.md` before proposing a cloud shape.
- Read `references/deployment-matrix.md` when comparing deployment options.
- Run `scripts/topology_component_map.py <repo-root>` to summarize likely deployable surfaces in BotBoy.
