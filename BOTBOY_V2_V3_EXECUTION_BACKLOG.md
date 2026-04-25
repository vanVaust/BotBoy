# BOTBOY V2 V3 Execution Backlog

## Purpose

This backlog converts [BOTBOY_V2_V3_MASTER_PROGRAM.md](./BOTBOY_V2_V3_MASTER_PROGRAM.md) into an operational build order for the combined v2 and v3 program.

The rule set for this backlog is:

- preserve current v1 contracts
- add capability versionably
- keep static worker profiles separate from runtime worker nodes
- prefer additive schema changes
- keep FastAPI and stdlib gateway surfaces in parity
- do not start v3 adaptive logic before v2 runtime, security, and observability exist

## Current Baseline

v1 is already release-grade and verified. The remaining work is the first distributed-runtime slice and its follow-on productization layer.

Current non-goals for this backlog:

- no v4 mission autonomy
- no self-modifying orchestration
- no enterprise multi-tenancy
- no cloud-only rewrite
- no opaque ML-only routing for critical decisions

## Execution Model

The backlog is organized into ordered epics. Each epic contains work packages with explicit dependencies and acceptance criteria.

Legend:

- `P0` = blocks forward progress
- `P1` = high value, next in line
- `P2` = important but not blocking the immediate slice

## Epic Map

| Epic | Scope | Priority | Dependency |
|---|---|---:|---|
| E1 | v2 worker fabric foundation | P0 | none |
| E2 | v2 runtime and queue control plane | P0 | E1 |
| E3 | v2 security and observability hardening | P1 | E1, E2 |
| E4 | v2 operator and API surface expansion | P1 | E1, E2, E3 |
| E5 | v3 workflow IR and policy runtime scaffolding | P1 | E2, E3, E4 |
| E6 | v3 knowledge and routing primitives | P2 | E5 |
| E7 | contract evolution, migrations, rollout discipline | P0 | E1 onward |

## Step 1 - Worker Fabric Foundation

This is the first required slice. It must land as a coherent vertical increment.

### Work Package 1.1 - Migration package and schema versioning

Goal:

- introduce a migration runner and schema version tracking
- keep current TaskStore schema additive-first

Deliverables:

- `botboy/migrations/` package
- schema version table
- migration registry and runner
- startup hook from TaskStore

Dependencies:

- existing SQLite mixin and TaskStore initialization

Acceptance criteria:

- a fresh database applies all migrations automatically
- an existing database can be opened without data loss
- schema version is recorded and queryable
- repeated startup is idempotent

Metrics:

- startup migration time
- number of applied migrations
- migration failures per test run

Risks:

- accidental schema drift between tests and production paths
- hidden assumptions in TaskStore bootstrap logic

### Work Package 1.2 - Worker node persistence tables

Goal:

- add runtime worker-node state without changing static worker profiles

Tables:

- `worker_nodes`
- `worker_heartbeats`
- `worker_capabilities`
- `execution_queues`
- `queue_leases`
- `dispatch_events`

Dependencies:

- Work Package 1.1

Acceptance criteria:

- node registration persists and reloads correctly
- heartbeat updates the active record
- queue and lease rows can be inserted and queried
- the static worker registry remains untouched

Metrics:

- node count
- heartbeat age
- lease count
- queue depth

Risks:

- mixing logical workers with physical nodes
- creating two competing sources of truth

### Work Package 1.3 - Worker node repository API

Goal:

- expose repository methods on TaskStore for worker-node lifecycle

Required methods:

- `register_worker_node(...)`
- `heartbeat_worker_node(...)`
- `list_worker_nodes(...)`
- `get_worker_node(...)`
- `drain_worker_node(...)`
- `list_execution_queues(...)`

Dependencies:

- Work Package 1.2

Acceptance criteria:

- repository methods are unit tested
- methods return stable dict or record shapes
- drain state is persisted and reflected in reads

Metrics:

- method coverage
- node lifecycle test pass rate

Risks:

- leaking SQLite-specific assumptions into higher layers

### Work Package 1.4 - CLI worker node commands

Goal:

- add top-level CLI support for worker-node operations

Commands:

- `botboy worker node list`
- `botboy worker node register`
- `botboy worker node heartbeat`
- `botboy worker node drain`

Dependencies:

- Work Package 1.3
- CLI parser and runtime dispatch

Acceptance criteria:

- parser accepts the new grammar
- runtime passthrough reaches the live BotBoy instance
- output is stable and human-readable

Metrics:

- command success rate
- parser coverage

Risks:

- breaking existing `worker list` behavior

### Work Package 1.5 - API v2 worker-node endpoints

Goal:

- add versioned HTTP endpoints for worker-node registration and heartbeat

Endpoints:

- `POST /api/v2/workers/register`
- `POST /api/v2/workers/heartbeat`
- `GET /api/v2/workers/nodes`
- `POST /api/v2/workers/{node_id}/drain`

Dependencies:

- Work Packages 1.2 and 1.3
- FastAPI gateway context wiring
- stdlib gateway parity

Acceptance criteria:

- endpoints exist in FastAPI and stdlib surfaces
- request auth follows current gateway policy
- responses are deterministic and documented by tests
- existing `/api/workers` contracts remain unchanged

Metrics:

- endpoint parity count
- live route coverage
- error rate by route

Risks:

- route duplication with existing worker summary endpoints
- parity drift between FastAPI and stdlib

### Work Package 1.6 - Observability primitives

Goal:

- add the minimal evidence chain for worker-fabric operations

Deliverables:

- dispatch events
- node heartbeat age
- queue depth metrics
- lease expiration metrics
- worker-node summary in `/api/status`

Dependencies:

- Work Packages 1.2 and 1.5

Acceptance criteria:

- status output includes worker-fabric state
- tests can assert queue and lease visibility
- dashboard payload exposes the new metrics without breaking old keys

Metrics:

- heartbeat freshness
- queue depth
- stale lease count
- dispatch event count

Risks:

- overloading the status payload too early

## Epic 2 - Runtime and Queue Control Plane

This epic builds on the worker fabric foundation and introduces actual distributed runtime control.

### Work Package 2.1 - Queue lifecycle and lease management

Goal:

- model queue allocation, lease acquisition, lease renewal, and lease release

Dependencies:

- Epic 1

Acceptance criteria:

- a node can acquire and renew a lease
- expired leases are visible and recoverable
- queue state survives restart

### Work Package 2.2 - Worker dispatch flow

Goal:

- route tasks to worker nodes through explicit queue state

Dependencies:

- Work Package 2.1

Acceptance criteria:

- dispatch events are recorded
- task ownership and node assignment stay consistent
- worker node drain prevents new assignments

### Work Package 2.3 - Recovery and reassignment

Goal:

- recover stale leases and reassign work safely

Dependencies:

- Work Packages 2.1 and 2.2

Acceptance criteria:

- stale leases can be listed and recovered
- reassignment preserves task history
- no silent task loss occurs

## Epic 3 - Security and Observability Hardening

### Work Package 3.1 - Scoped machine principals

Goal:

- distinguish human, admin, and machine principals for worker nodes

Dependencies:

- Epic 1

Acceptance criteria:

- node registration requires an explicit identity
- access is fail-closed when auth is enabled

### Work Package 3.2 - Worker-fabric audit trail

Goal:

- record who registered, heartbeated, drained, and reassigned nodes

Dependencies:

- Epic 1, Epic 2

Acceptance criteria:

- audit records are queryable
- live tests can verify audit visibility

### Work Package 3.3 - Metrics and dashboard expansion

Goal:

- expose worker fabric metrics in the control center and API status payload

Dependencies:

- Epic 1, Epic 2

Acceptance criteria:

- dashboard shows worker nodes, queues, and leases
- old dashboard fields remain intact

## Epic 4 - Operator and API Surface Expansion

### Work Package 4.1 - Node inspection surfaces

Goal:

- add detailed node inspection for CLI and HTTP

Dependencies:

- Epic 1, Epic 2

Acceptance criteria:

- operator can inspect a node, its last heartbeat, queue assignment, and drain state

### Work Package 4.2 - Control actions

Goal:

- add controlled node drain, resume, and lifecycle commands

Dependencies:

- Epic 2, Epic 3

Acceptance criteria:

- control actions are explicit and auditable

### Work Package 4.3 - Backward compatibility checks

Goal:

- ensure no regression in existing task, worker, memory, or auth routes

Dependencies:

- Epic 1 through Epic 4

Acceptance criteria:

- existing tests remain green
- live route surface remains compatible

## Epic 5 - v3 Workflow IR and Policy Runtime Scaffolding

v3 starts only after v2 runtime, lease discipline, and observability are real.

### Work Package 5.1 - Workflow IR skeleton

Goal:

- introduce typed workflow definitions without replacing the current command runtime

Dependencies:

- Epic 2, Epic 3

Acceptance criteria:

- workflow defs can be stored, listed, and validated

### Work Package 5.2 - Policy objects and decisions

Goal:

- represent policy bindings and decision records

Dependencies:

- Work Package 5.1

Acceptance criteria:

- policy decisions are logged with reasons
- operator can inspect policy impact

### Work Package 5.3 - Planner hooks

Goal:

- introduce planner-facing hooks for workflow selection and approval boundaries

Dependencies:

- Work Package 5.1, Work Package 5.2

Acceptance criteria:

- planner hook output is deterministic and testable

## Epic 6 - Knowledge and Routing Primitives

### Work Package 6.1 - Knowledge graph storage

Goal:

- add minimal entity-edge-fact storage for later retrieval and routing

Dependencies:

- Epic 5

Acceptance criteria:

- entities and edges can be stored and queried

### Work Package 6.2 - Routing decision evidence

Goal:

- record why a command or workflow was routed a certain way

Dependencies:

- Work Package 6.1

Acceptance criteria:

- routing decisions have traceable input and output metadata

## Epic 7 - Contract Evolution and Rollout Discipline

### Work Package 7.1 - Compatibility gates

Goal:

- lock in additive-first contract evolution

Dependencies:

- all previous epics

Acceptance criteria:

- breaking change attempts fail tests or review gates

### Work Package 7.2 - Upgrade and rollback path

Goal:

- make data and schema upgrades reversible where possible

Dependencies:

- Epic 1, Epic 2

Acceptance criteria:

- rollback instructions exist and are tested for the first migration set

### Work Package 7.3 - Release readiness

Goal:

- keep the v2 and v3 program deployable and testable at every major increment

Dependencies:

- all previous epics

Acceptance criteria:

- full suite remains green
- release acceptance stays green
- live gateway parity remains green

## Ordered Build Plan

1. Finish Epic 1 Work Packages 1.1 through 1.6.
2. Finish Epic 2 Work Packages 2.1 through 2.3.
3. Finish Epic 3 Work Packages 3.1 through 3.3.
4. Finish Epic 4 Work Packages 4.1 through 4.3.
5. Start Epic 5 only after worker fabric, leases, and observability are stable.
6. Add Epic 6 primitives only after workflow IR exists.
7. Keep Epic 7 gates live throughout the whole program.

## Suggested First Milestone

The first milestone should be considered done only when all of the following are true:

- migrations package exists
- worker node tables exist
- repository APIs exist
- CLI worker node commands exist
- API v2 worker-node endpoints exist in both gateway surfaces
- observability primitives are visible in status and dashboard payloads
- targeted tests for the above are green

## Operational Metrics

Track these during implementation:

- test suite pass count
- route parity count
- migration application count
- worker node count
- queue depth
- stale lease count
- heartbeat freshness
- API error count by route

## Risk Register

| Risk | Impact | Mitigation |
|---|---|---|
| Static worker profiles get confused with runtime nodes | high | keep separate tables, separate payloads, separate commands |
| FastAPI and stdlib drift apart | high | implement both surfaces in the same slice |
| Schema changes break release installs | high | additive-first migrations, targeted acceptance tests |
| Observability becomes noisy before useful | medium | expose only a minimal set of metrics in Step 1 |
| v3 starts too early | high | enforce Epic 5 dependency gates |

## Definition Of Done For This Backlog

The backlog is not complete until:

- Step 1 is implemented end-to-end
- the first distributed runtime slice is deployed through both gateway surfaces
- tests cover migration, CLI, API, and live parity
- the next epic can be started without rewriting the foundation

