# Topology Principles

Use these principles:

1. keep the control plane explainable
2. preserve local-first fallback paths where feasible
3. keep auth, tracing, and eval surfaces coherent across boundaries
4. move components remote only when there is a clear operational reason
5. design for rollback, not only for scale

Map every component to one of these roles:

- local control
- shared service
- remote execution
- storage
- observability
