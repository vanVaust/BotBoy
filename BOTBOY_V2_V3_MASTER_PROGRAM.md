# BotBoy V2-V3 Master Program

```yaml
document_type: master_program
subject: botboy_v2_v3_combined_block
baseline_version: v1
baseline_status:
  classification: release-grade_local-first_single-host_agent_platform
  verified_tests: "python -m unittest discover -s tests -v -> Ran 157 tests, OK"
  release_acceptance: verified
  gateway_modes:
    stdlib: verified_release_live
    fastapi: verified_release_live
program_goal:
  v2: productized_distributed_runtime
  v3: adaptive_orchestration_platform
combined_outcome: multi-host_capable_policy_governed_explainable_agent_execution_platform
authoring_mode: grounded_from_current_repo_state_plus_parallel_agent_synthesis
hard_invariants:
  - local_first_remains_first_class
  - fail_closed_security_defaults_remain_mandatory
  - cli_api_mcp_web_contracts_evolve_conservatively
  - all_major_actions_remain_traceable_replayable_auditable
  - autonomy_does_not_outrun_policy_review_or_evidence
non_goals:
  - v4_level_autonomy
  - enterprise_multi_tenant_fabric
  - marketplace_economics
  - self_modifying_production_orchestration
  - cloud_only_rearchitecture
```

## 1. Purpose

This document is the **canonical combined roadmap for BotBoy v2 and v3 as one coherent build program**.

It is intentionally more precise and operational than the broader `v2-v7` roadmap files. Its purpose is to define:

- exactly what the combined `v2+v3` block is
- why `v2` and `v3` should be treated as one staged program
- what architecture must exist at the end of the block
- what code, data, interfaces, and operations have to change
- what must be validated before the block is considered complete
- what must explicitly stay out of scope

This is not a speculative vision note. It is a **build program**.

## 2. Grounded Baseline

BotBoy `v1` is already a completed product core with the following verified properties:

- local-first
- single-host
- release-grade
- CLI
- stdlib gateway
- optional FastAPI gateway
- MCP server
- persistent task, trace, history, and scheduler stores
- merge review and worker handoff model
- packaged release
- clean install acceptance
- CI verification
- `open_priorities = []`
- `known_gaps = []`
- documented full suite result: `Ran 157 tests, OK`

### Why this matters

The combined `v2+v3` program does **not** start from a prototype. It starts from a stable, testable, distributable product core.

That changes the nature of the work:

- the program is not about "making BotBoy real"
- the program is about **changing the operating class of BotBoy**

## 3. Why v2 and v3 are one combined block

`v2` and `v3` should not be treated as unrelated versions.

### `v2` alone does not create the final strategic value

`v2` by itself mainly makes BotBoy:

- multi-host capable
- more secure
- more operable
- more supportable

That is necessary, but not sufficient for the real next product jump.

### `v3` depends directly on `v2`

`v3` requires:

- stable remote worker semantics
- explicit control-plane authority
- richer evidence chains
- durable topology and capability models
- reliable rollout and rollback mechanics

Without those, adaptive orchestration becomes fragile and unsafe.

### Combined program logic

The correct combined logic is:

1. Build `v2` infrastructure, contracts, and operational discipline.
2. Then layer `v3` intelligence on top of those stable primitives.
3. Ship them as **one large staged program** with several releasable trains.

## 4. Combined Program Mission

The combined `v2+v3` program mission is:

> Transform BotBoy from a release-grade local single-host agent platform into a production-grade, multi-host-capable, policy-governed, explainable orchestration platform with typed workflow execution and adaptive delegation.

## 5. Program Outcomes

At the end of the combined block, BotBoy must be able to do all of the following:

1. Run work across multiple worker nodes under explicit leases and heartbeats.
2. Treat artifacts as transportable, integrity-checked objects rather than implicit local paths.
3. Enforce stronger role, principal, token, and machine identity controls.
4. Expose queue, worker, artifact, and incident state through real operator surfaces.
5. Represent work as typed workflows rather than only ad hoc command routing.
6. Evaluate policies at runtime for delegation, merge, retry, and escalation decisions.
7. Record decision evidence such that routing and policy outcomes are explainable and replayable.
8. Maintain memory and knowledge structures above raw trace logs.
9. Improve routing and delegation quality under measurable constraints of cost, latency, and confidence.
10. Preserve backward compatibility for the current product surfaces while evolving contracts versionably.

## 6. Program Non-Negotiables

These are mandatory constraints for every implementation phase:

1. Local-first remains first-class.
2. Security remains fail-closed.
3. Existing CLI/API/MCP/web semantics remain conservative by default.
4. Replayability and evidence chains are mandatory for important decisions.
5. Any new intelligence layer must reduce operator burden, not merely add complexity.
6. Observability must be designed as part of the runtime, not bolted on afterward.
7. Rollback must exist before riskier rollout states are allowed.

## 7. Combined End-State Architecture

The combined end-state should be understood as five cooperating planes.

## 7.1 Interface Layer

Surfaces that users and systems interact with:

- CLI
- stdlib gateway
- FastAPI gateway
- MCP server
- Control Center UI

This layer remains the public interaction shell. It should evolve conservatively and remain coherent.

## 7.2 Control Plane

The control plane owns:

- auth and identity evaluation
- policy evaluation
- workflow compilation and planning
- scheduling and execution assignment
- capability registry
- topology and worker state registry
- decision evidence

The control plane must remain:

- deterministic
- traceable
- replayable
- conservative under uncertainty

## 7.3 Execution Plane

The execution plane owns:

- local worker execution
- remote worker execution
- step-level task execution
- heartbeat and lease maintenance
- artifact production and handoff

The execution plane must not self-authorize sensitive actions. It acts under control-plane authority.

## 7.4 Data Plane

The data plane owns:

- task store
- task events
- task artifacts
- trace/history/scheduler persistence
- workflow definitions and workflow runs
- policy data and decision records
- memory and knowledge graph structures

The data plane should remain additive-first during migration.

## 7.5 Observability and Governance Plane

This plane owns:

- metrics
- structured logs
- traces
- replay
- simulation
- incident diagnosis
- evidence bundles

This is the layer that makes `v3` safe enough to trust.

## 8. Domain Boundaries

The combined program should be built around explicit bounded contexts.

## 8.1 Execution Orchestration

Owns:

- task lifecycle
- queue dispatch
- lease ownership
- retry behavior
- merge trigger points

## 8.2 Policy and Governance

Owns:

- policy definitions
- policy compilation
- policy evaluation
- deny/allow/review outcomes
- obligations and reason codes

## 8.3 Workflow and Planning

Owns:

- workflow IR
- task decomposition
- checkpoints
- planning state
- plan revisions

## 8.4 Memory and Knowledge

Owns:

- session memory
- project memory
- skill memory
- operational memory
- knowledge entities and edges
- provenance links

## 8.5 Identity and Access

Owns:

- principals
- roles
- scoped tokens
- machine identities
- API keys and JWT context

## 8.6 Runtime Fabric

Owns:

- worker registration
- worker capabilities
- worker health
- drain and retirement
- topology awareness

### Boundary rule

No context should perform ad hoc direct writes into another context's state model. Coordination should go through contracts and events.

## 9. Program Structure

This is the recommended program spine.

## 9.1 Release Trains

The combined block should ship as four releasable trains:

| Train | Name | Primary Outcome |
|---|---|---|
| `A` | `v2 foundation` | remote worker protocol, queue/lease model, environment profile framework |
| `B` | `v2 operability` | RBAC, secrets, observability, migration, rollback discipline |
| `C` | `v3 policy core` | workflow IR skeleton, policy engine, decision records |
| `D` | `v3 adaptive` | knowledge graph, adaptive routing, replay/simulation-backed orchestration |

Each train must be independently releasable.

## 9.2 Program Phases

These are the practical execution phases.

| Phase | Purpose | Train Affinity |
|---|---|---|
| `P0` | program guardrails and architecture freeze | pre-train |
| `P1` | distributed runtime foundation | A |
| `P2` | security, observability, rollout, migration | B |
| `P3` | workflow IR and policy runtime | C |
| `P4` | knowledge graph and adaptive delegation | D |
| `P5` | final acceptance, rollback proof, evidence freeze | post-D |

## 10. Architecture Decisions To Freeze Early

These decisions should be explicitly written as ADRs before deep implementation.

1. Control-plane vs execution-plane split.
2. Worker protocol versioning and compatibility window.
3. Artifact URI model.
4. Event-first evidence schema.
5. Workflow IR schema versioning.
6. Policy decision contract.
7. Contract deprecation rules for CLI/API/MCP.
8. Replay compatibility rules.
9. Environment profile model.
10. Rollback and migration discipline.

## 11. Code and Package Strategy

The combined block should introduce clear package streams without breaking current facades.

## 11.1 New package streams

Recommended additions under `botboy/`:

- `control_plane/`
- `execution_plane/`
- `policy/`
- `workflow/`
- `knowledge/`
- `transport/`
- `ops/`
- `migrations/`

## 11.2 Existing entrypoints that should remain facades

- `botboy/__main__.py`
- `botboy/cli.py`
- `botboy/gateway/server.py`
- `botboy/gateway/simple_server.py`
- `botboy/runtime.py`

## 11.3 Existing modules most likely to change

- `botboy/tasks.py`
- `botboy/workers.py`
- `botboy/delegation_advisor.py`
- `botboy/intelligence_route_support.py`
- `botboy/evals.py`
- `botboy/gateway/routes_core.py`
- `botboy/gateway/routes_tasks.py`
- `botboy/gateway/routes_auth.py`
- `botboy/release_acceptance.py`
- `botboy/core/config.py`

## 12. Data Model Evolution

The data strategy must be additive-first.

## 12.1 New table families

Recommended new tables:

- `worker_nodes`
- `worker_heartbeats`
- `worker_capabilities`
- `execution_queues`
- `queue_leases`
- `dispatch_events`
- `artifacts_v2`
- `policies`
- `policy_versions`
- `policy_bindings`
- `policy_decisions`
- `workflow_defs`
- `workflow_versions`
- `workflow_runs`
- `workflow_steps`
- `knowledge_entities`
- `knowledge_edges`
- `knowledge_facts`
- `provenance_links`
- `routing_decisions`
- `simulation_runs`
- `evaluation_scores`

## 12.2 Migration pattern

1. Add schema version tracking.
2. Add tables and nullable columns first.
3. Dual-write into legacy and new structures.
4. Backfill historical data.
5. Switch reads behind feature flags.
6. Remove dual-write only after acceptance cycles pass.

## 12.3 Feature flags

Recommended flags:

- `BOTBOY_V2_WORKER_FABRIC`
- `BOTBOY_V2_POLICY_RUNTIME`
- `BOTBOY_V3_WORKFLOW_IR`
- `BOTBOY_V3_KNOWLEDGE_GRAPH`

## 13. API and CLI Evolution

## 13.1 Contract rule

Current surfaces stay intact. New capability is added versionably.

## 13.2 API strategy

Keep current `/api/*` and add versioned groups:

- `/api/v2/workers/*`
- `/api/v2/queues/*`
- `/api/v2/artifacts/*`
- `/api/v2/policies/*`
- `/api/v3/workflows/*`
- `/api/v3/knowledge/*`
- `/api/v3/routing/*`

## 13.3 CLI strategy

Add but do not break:

- `botboy worker node ...`
- `botboy queue ...`
- `botboy policy ...`
- `botboy workflow ...`
- `botboy knowledge ...`
- `botboy route explain ...`
- `botboy simulate plan ...`

## 14. Workstreams

The combined block is best executed as eight parallel but dependency-aware workstreams.

| Workstream | Scope |
|---|---|
| `WS-A Runtime Fabric` | worker protocol, registration, heartbeat, drain, leases, artifact transport |
| `WS-B Security and Identity` | RBAC, scoped tokens, machine principals, fail-closed hooks |
| `WS-C Observability and Evidence` | event taxonomy, evidence chain, replay upgrades |
| `WS-D Workflow IR and Planner` | typed workflow graph, checkpoints, planner hooks |
| `WS-E Policy Engine` | v2 auth policy, v3 orchestration policy, obligations, reason codes |
| `WS-F Memory Graph` | tiered memory, graph projection, retrieval APIs |
| `WS-G Contract Evolution` | versioning, migration, compatibility enforcement |
| `WS-H Operator Surfaces` | queue/worker/policy/delegation visibility in Control Center |

## 15. Step-by-Step Build Order

This is the strict recommended implementation order.

## Step 1. Program Guardrails

Deliver:

- migration runner
- schema version table
- feature flags
- acceptance harness skeleton
- initial ADR bundle

Reason:

Everything else depends on controlled change and rollback.

## Step 2. V2 Worker Fabric Core

Deliver:

- worker registration
- heartbeat protocol
- queue dispatch
- lease ownership
- worker capability contract

Reason:

This is the minimum infrastructure change that turns BotBoy from single-host assumption into multi-host-capable runtime.

## Step 3. V2 Artifact and Execution Data Plane

Deliver:

- URI-based artifacts
- remote-capable artifact metadata
- integrity checks
- lineage model

Reason:

Remote execution without artifact discipline is structurally weak.

## Step 4. V2 Security and Identity Hardening

Deliver:

- scoped machine principals
- policy-bound API keys
- role enforcement on new routes
- environment-profile security controls

Reason:

Remote execution increases blast radius. Security must rise before scaling further.

## Step 5. V2 Observability and Operator Ops Surfaces

Deliver:

- queue metrics
- worker health views
- artifact throughput/error views
- incident drilldown
- operator control panels

Reason:

Distributed runtime without observability is not a product.

## Step 6. V2 Productization and Upgrade Paths

Deliver:

- profile modes: `local`, `team`, `prod`
- migration flows
- rollback scripts
- release acceptance expansion
- remote-worker rollout and canary logic

Reason:

This closes the `v2` productization layer.

## Step 7. V3 Workflow IR

Deliver:

- workflow definition model
- workflow run state
- checkpoint model
- deterministic serialization

Reason:

Adaptive orchestration cannot stay ad hoc. It needs explicit executable structure.

## Step 8. V3 Policy Runtime

Deliver:

- policy compiler/evaluator
- policy bindings
- decision records
- policy reason codes and obligations

Reason:

This is the control logic that allows adaptive routing without unsafe improvisation.

## Step 9. V3 Knowledge Graph and Provenance

Deliver:

- entity-edge model
- provenance links from tasks/traces/artifacts/decisions
- query APIs

Reason:

Adaptive decisions need explainable context, not just heuristics.

## Step 10. V3 Adaptive Delegation and Explainability

Deliver:

- routing that uses policy, graph, and cost/latency signals
- explain endpoints
- operator intervention queue

Reason:

This is the actual strategic `v3` leap.

## Step 11. V3 Replay and Simulation Harness

Deliver:

- deterministic replay
- counterfactual strategy comparison
- policy branch replay
- route quality evaluation

Reason:

Without replay and simulation, adaptive behavior cannot be trusted.

## Step 12. Final Combined Stabilization

Deliver:

- legacy path cleanup
- docs and runbooks
- final acceptance matrix
- release evidence bundles

Reason:

This closes the combined program as a releasable block, not just a pile of implemented features.

## 16. Epics

The combined block should be managed through these epics:

- `EPIC-V2-01 Distributed Worker Runtime`
- `EPIC-V2-02 Queue and Lease Reliability`
- `EPIC-V2-03 Artifact Plane V2`
- `EPIC-V2-04 Security and Principal Model V2`
- `EPIC-V2-05 Observability and Incident Operations`
- `EPIC-V2-06 Upgrade, Migration, and Deployment Profiles`
- `EPIC-V3-01 Workflow IR and Execution Graph`
- `EPIC-V3-02 Policy Compiler and Decision Runtime`
- `EPIC-V3-03 Knowledge Graph and Provenance`
- `EPIC-V3-04 Adaptive Routing and Delegation Intelligence`
- `EPIC-V3-05 Replay, Simulation, and Evaluation Intelligence`
- `EPIC-V3-06 Operator Explainability and Review Queue V3`

Each epic should produce:

- code
- migration logic
- tests
- rollback behavior
- operator-facing evidence

## 17. Validation and Release Gates

## 17.1 V2 gate

Must prove:

- three-node distributed execution
- RBAC works
- artifact lineage works
- upgrade and rollback work

## 17.2 V3 gate

Must prove:

- same objective can route differently under different policies
- divergence is deterministic
- divergence is explainable
- replay can reconstruct the reason chain

## 17.3 Final block gate

Must prove:

- no regression on current CLI/API/MCP/web contracts
- full acceptance green
- replay stability green
- security fail-closed paths green
- rollout and rollback evidence complete

## 18. Validation Framework

The following test classes are mandatory:

- unit
- integration
- contract
- end-to-end
- replay/simulation
- security
- performance
- resilience
- migration

Important quantitative gates from the validation stream:

- multi-worker execution success `>= 99.5%`
- lease conflict rate `< 0.1%`
- unauthorized access success `0%`
- policy decision determinism `>= 99.9%`
- replay reproducibility `>= 99%`
- route quality uplift vs v2 baseline `>= 15%`

## 19. Performance and Scalability Program

The combined block must stay inside explicit latency and throughput budgets.

## 19.1 Request classes

- `Interactive`
- `Control-path`
- `Background`

## 19.2 Initial budgets

- interactive read-heavy p95 `< 300 ms`
- command submission p95 `< 700 ms`
- control-path decision p95 `< 150 ms` in `v2`
- control-path decision p95 `< 250 ms` in `v3`
- queue dispatch lag p95 `< 2 s`
- small artifact retrieval p95 `< 1.5 s`

## 19.3 Optimization tracks

- queue metrics and lease fairness
- artifact tiering, compression, checksum, dedup
- cardinality-safe observability
- compiled-and-cached policy evaluation
- graph write/read split
- admission control and capacity budgets

## 19.4 Performance acceptance

Must pass:

- 24h soak without unstable growth
- `2x` nominal load with p95 budgets intact
- worker loss without retry storm
- artifact stress integrity and latency
- policy engine benchmark within target
- graph hot-path benchmark within target

## 20. Integration, Rollout, and Release Readiness

## 20.1 Environment profiles

Canonical profiles:

- `local-dev`
- `team-staging`
- `prod-single-region`
- `prod-dr`

## 20.2 Install / upgrade / rollback

Required:

- clean install path
- cluster bootstrap path
- blue/green control-plane upgrade
- rolling worker upgrade with compatibility window
- dry-run migration report
- point-in-time restore plus replay sanity

## 20.3 Documentation that must ship

- deployment runbooks per profile
- upgrade guide
- worker operations handbook
- policy authoring guide
- control-center guide
- incident playbooks

## 20.4 Evidence package

Each phase should publish:

- `phase-summary.md`
- `test-results.json`
- `security-report.json`
- `replay-report.json`
- `perf-report.json`
- `known-risks.md`
- `waivers.md`
- `artifact-manifest.json`

## 21. Operator Surface Requirements

The combined block must expand the Control Center to show:

### V2 surfaces

- worker health and leases
- queue depth and dispatch lag
- artifact availability and integrity
- migration and upgrade status
- incident drilldown

### V3 surfaces

- policy reason chain
- decision provenance
- adaptive routing confidence and cost impact
- intervention queue
- replay vs simulation compare views

## 22. Risks

Top risks:

1. Lease race causes duplicate execution.
2. Policy complexity introduces non-determinism.
3. Workflow IR is overdesigned before runtime semantics stabilize.
4. Graph lookups hurt hot-path latency.
5. CLI/API/MCP contracts drift.
6. Observability volume creates operational cost and noise.
7. Remote rollout outruns supportability.

## 23. Explicit Anti-Goals

The combined block must not absorb:

- unconstrained autonomy loops
- v4 mission autonomy
- enterprise multi-tenancy
- economics marketplace logic
- self-modifying production orchestration
- cloud-only rewrites
- opaque ML-only routing for critical decisions

## 24. Practical First Sprint

The first sprint should do exactly this:

1. Create `botboy/migrations` with schema version table.
2. Add `worker_nodes`, `execution_queues`, `queue_leases`.
3. Add `/api/v2/workers/register` and `/api/v2/workers/heartbeat`.
4. Add CLI `botboy worker node list/register/drain`.
5. Add distributed lease lifecycle integration tests.
6. Add queue and worker observability primitives.
7. Write ADR-001 through ADR-003.

## 25. Final Program Verdict

This combined `v2+v3` program is technically coherent and realistic because it respects a strict order:

1. deterministic distributed runtime first
2. secure and observable productization second
3. typed workflows and policy control third
4. adaptive intelligence only after evidence, replay, and operator intervention exist

That sequencing is the difference between a real platform evolution and an unstable feature pile."}}
