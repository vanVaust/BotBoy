# BotBoy V2.5 To V5 Execution Roadmap

Last updated: 2026-04-25
Owner: active execution program
Status: v2.5 runtime gates green; v3 live workflow runtime next
Scope: from current v2/v3 transition state to v5 GA

## 1. Purpose

This file is the active execution roadmap from the current verified BotBoy baseline to `v5`.

It exists for one reason:

- turn the current local-first, release-capable BotBoy build into a disciplined progression through
  - `v2.5` runtime completion
  - `v3` live workflow and policy runtime
  - `v4` governed autonomy and incident control
  - `v5` tenant-safe control plane, fleet operations, quotas, and economics

This document supersedes older broad strategy files as the practical build order for work from now onward. Older roadmap documents still provide context, but this file is the execution source of truth for sequencing, gates, ownership, and acceptance.

## 2. Current Baseline

Date of baseline: `2026-04-25`
Workspace: `botboi_finished`
Current wave: `Welle 22`

Verified commands:

- `python -m unittest discover -s tests -v` -> `Ran 247 tests, OK`
- `python -m botboy.release_smoke -v` -> `Ran 6 tests, OK`
- `python -m botboy evals --summary` -> replay coverage `6/6`, pass rate `1.000`

Current open priorities from `botboy/data/STATUS_SNAPSHOT.json`:

- `live_workflow_ir_policy_persistence`

Current reality:

- BotBoy is already release-capable for local-first operation.
- Queue, lease, dispatch, and worker daemon infrastructure exist.
- Worker handoff now uses the canonical queue-backed daemon claim/report path for child task execution.
- Workflow IR exists, but is not yet a complete live persisted runtime.
- Remote readiness and auth hardening exist, but remote distributed operation is not yet a finished product path.
- Packaging and source-tree isolation gates pass; GitHub publication remains a separate explicit release action.

What BotBoy is now:

- a strong single-host orchestrator with a canonical local worker fabric

What BotBoy is not yet:

- a complete distributed execution fabric
- a policy-governed autonomy runtime
- a tenant-safe fleet platform

## 3. Non-Negotiables

These rules govern every version from here to `v5`:

- local-first remains first-class
- contracts evolve additively and versionably
- security-sensitive paths fail closed
- every new behavior has deterministic evidence and replay where relevant
- approvals, policy, audit, and rollback exist before high-risk autonomy
- packaging and release isolation are treated as product features, not cleanup work
- no `v6` or `v7` scope is allowed to leak into `v2.5` to `v5` execution

## 4. Version Targets

### 4.1 V2.5 Mission

Finish the runtime fabric so handoff, dispatch, lease, worker execution, and contract parity form one canonical path.

`v2.5` is complete when:

- queue-backed handoff is the trusted end-to-end path
- lease semantics are hardened against duplication and stale writers
- CLI, stdlib, FastAPI, and MCP surfaces are contract-aligned where intended
- public release packaging is isolated from local development artifacts

Current checkpoint: these `v2.5` implementation and packaging gates are green as of `2026-04-25`; the next engineering focus is `v3` live workflow and policy persistence.

### 4.2 V3 Mission

Turn Workflow IR and policy decisions into live persisted runtime primitives with deterministic replay and operator inspection.

`v3` is complete when:

- workflow definitions persist safely
- policy decisions persist with reason codes and obligations
- traces, tasks, history, and replays correlate by `workflow_id`
- replay can explain what happened and why

### 4.3 V4 Mission

Add controlled autonomy with approvals, incident handling, pause or resume, and rollback as runtime obligations rather than operator folklore.

`v4` is complete when:

- high-risk actions are approval-gated
- incidents can pause, quarantine, resume, or rollback live missions
- audit evidence is durable and tamper-evident enough for operator trust
- scheduler behavior remains bounded and observable under failure

### 4.4 V5 Mission

Add tenant-safe control, governance inheritance, usage accounting, quotas, and cost-aware routing so BotBoy can operate as a controlled organizational platform.

`v5` is complete when:

- organization, team, project, and workflow boundaries are enforced
- governance inheritance is explicit and testable
- usage, quotas, and budgets are measurable and enforceable
- operators can steer fleets and workloads without cross-tenant bleed

## 5. Seven-Agent Operating Model

Every step below is orchestrated for seven parallel expert strands using `gpt-5.3-codex`.

| Agent | Role | Primary ownership | Must not own |
|---|---|---|---|
| `A1` | Senior Software Architect | target architecture, ADRs, bounded contexts, interface boundaries | feature-specific implementation details that belong to execution agents |
| `A2` | Senior Distributed Systems Engineer | worker fabric, leases, transport, dispatch, recovery, scheduling | tenant product policy |
| `A3` | Senior Security Architect | auth, approval, policy enforcement, audit chain, release hardening | UX detail work |
| `A4` | Senior Backend Engineer | TaskStore, gateway parity, CLI, MCP, workflow persistence, replay integration | cross-version product sequencing |
| `A5` | Senior QA and Release Engineer | gates, acceptance, fuzz, chaos, packaging, clean release repo checks | product design decisions |
| `A6` | Senior UI and UX Engineer | control center, operator workflows, dashboard contracts, incident and replay UX | back-end persistence schema ownership |
| `A7` | Principal Product and Systems Orchestrator | scope control, sequencing, dependency board, gate ownership, cut decisions | file-level implementation except to unblock program flow |

Shared execution rules:

- each agent owns a disjoint primary write area per sprint
- each sprint must land vertical slices, not abstract scaffolding alone
- each gate has one explicit owner and one verifying owner
- if a dependency blocks forward motion for more than one sprint, `A7` must split the scope or cut the feature

## 6. Gate Model

No version transition is allowed without the preceding gate passing.

| Gate | Meaning | Exit requirement |
|---|---|---|
| `G0` | Baseline integrity | tests, smoke, eval summary, and status snapshot all align |
| `G1` | Queue-backed handoff | delegated work runs through queue and lease path only, with recovery |
| `G2` | Workflow-policy persistence | workflow and policy decisions persist and survive restart |
| `G3` | Release isolation | publishable repo is clean, packaged, and path-safe |
| `G4` | V3 go-live | replay, policy, and routing are deterministic and inspectable |
| `G5` | V4 go-live | approval, incident, pause or resume, and rollback are live and verified |
| `G6` | V5 alpha | tenant-safe multi-team operation works without policy or data bleed |
| `G7` | V5 GA | quotas, usage, fleet control, and release hardening are operator-safe |

## 7. Macro Sequence

The roadmap is intentionally sequenced:

1. close the three open priorities
2. promote Workflow IR from eval artifact to live runtime
3. add governance and incident control
4. add tenant separation and organizational policy inheritance
5. add usage, quotas, economics, and fleet command capability

Anything outside that sequence is a distraction unless it directly unblocks one of those five moves.

## 8. Detailed Execution Backlog

Each work package below is written as the smallest effective implementation slice.

Columns:

- `ID`: unique work package id
- `Owner`: primary agent
- `Depends on`: explicit prerequisite
- `Action`: concrete engineering change
- `Done means`: hard completion criteria
- `Verify with`: required verification

### 8.1 Track R0: Foundation Closure

This track closes the three live open priorities and removes ambiguity from the runtime.

| ID | Owner | Depends on | Action | Done means | Verify with |
|---|---|---|---|---|---|
| `BB-R0-WP01` | `A1` | none | Freeze the canonical contract matrix for CLI, FastAPI, stdlib, and MCP. Write down which surfaces must remain identical and which are intentionally versioned or asymmetric. | A machine-readable contract canon exists and tests reference it. | targeted contract tests plus full `unittest` |
| `BB-R0-WP02` | `A4` | `BB-R0-WP01` | Add dispatch event canon and route all handoff initiation through the queue and dispatch path instead of any direct shortcut execution path. | A handoff command always writes dispatch intent and queued child work before execution starts. | handoff unit tests plus gateway route tests |
| `BB-R0-WP03` | `A2` | `BB-R0-WP02` | Unify lease source of truth, add fencing tokens, and harden report-result transaction behavior against duplicate or stale reporters. | One active lease semantics hold under race tests and stale result attempts fail safely. | lease tests, daemon tests, race-focused tests |
| `BB-R0-WP04` | `A2` | `BB-R0-WP03` | Finish queue-backed handoff end-to-end path including parent-child synchronization, result merge, and stale recovery. | Parent task state reflects child queue execution without inline bypass logic. | end-to-end handoff acceptance in CLI, FastAPI, stdlib |
| `BB-R0-WP05` | `A4` | `BB-R0-WP01` | Introduce workflow and policy persistence schema with additive migrations and stable identifiers. | Workflow definitions, policy decisions, and replay records persist in schema-backed storage. | migration tests, workflow persistence tests |
| `BB-R0-WP06` | `A4` | `BB-R0-WP05` | Move Workflow IR hooks from smoke and eval support into the live runtime path with restart-safe resume and correlation ids. | Live workflow execution writes persisted records and reloads them across restart. | workflow runtime tests and replay tests |
| `BB-R0-WP07` | `A5` | `BB-R0-WP01` | Isolate public release repository boundaries, remove local-path leakage risk, and define clean publishable tree requirements. | A clean release tree can be built and installed without workspace-specific assumptions. | build-install acceptance, packaging inspection, `pip check` |
| `BB-R0-WP08` | `A3` | `BB-R0-WP07` | Harden release provenance requirements: artifact identity, integrity checks, and fail-closed packaging validation. | Release pipeline fails if repo isolation or artifact provenance expectations are broken. | release workflow gate, artifact verification tests |

### 8.2 Track R1: V3 Core Runtime

This track turns Workflow IR and policy into live control primitives.

| ID | Owner | Depends on | Action | Done means | Verify with |
|---|---|---|---|---|---|
| `BB-R1-WP01` | `A1` | `G1`, `G2` | Define typed Workflow IR v1 contract for nodes, edges, checkpoints, obligations, and replay anchors. | Runtime and tests both use one explicit Workflow IR contract. | workflow contract tests |
| `BB-R1-WP02` | `A3` | `BB-R1-WP01` | Add policy compiler output with reason codes, obligations, deny reasons, and approval requirements. | Policy decisions are serializable and explainable, not ad-hoc booleans. | policy tests plus replay explanation tests |
| `BB-R1-WP03` | `A4` | `BB-R1-WP02` | Build deterministic replay diff harness that compares plan, policy, route, and outcome for one workflow execution. | Replays show stable divergence points with minimal false diffs. | replay tests, eval bundle |
| `BB-R1-WP04` | `A6` | `BB-R1-WP02` | Add operator intervention queue and workflow replay surfaces to the control center with safe read-first UX. | Operators can inspect waiting approvals, policy decisions, and replay summaries. | UI acceptance tests |
| `BB-R1-WP05` | `A2` | `BB-R1-WP03` | Introduce policy-aware routing inputs such as confidence, latency class, queue health, and execution capability. | Routing decisions use measured runtime facts but remain policy-bounded and replayable. | routing tests plus replay consistency tests |
| `BB-R1-WP06` | `A5` | `BB-R1-WP03`, `BB-R1-WP04`, `BB-R1-WP05` | Add live workflow consistency evals and gate them in CI. | `v3` core cannot regress without failing acceptance. | eval summary, targeted workflow acceptance, full suite |

### 8.3 Track R2: V4 Governance And Incident Control

This track adds controlled autonomy, not unrestricted autonomy.

| ID | Owner | Depends on | Action | Done means | Verify with |
|---|---|---|---|---|---|
| `BB-R2-WP01` | `A1` | `G4` | Define mission state machine for running, waiting approval, paused, quarantined, rolling back, completed, and failed states. | Mission lifecycle is explicit and persisted. | state machine tests |
| `BB-R2-WP02` | `A3` | `BB-R2-WP01` | Add autonomy envelopes and risk tiers that map actions to policy obligations and approval paths. | High-risk actions cannot execute without the required envelope and decision record. | approval and policy enforcement tests |
| `BB-R2-WP03` | `A4` | `BB-R2-WP02` | Implement approval circuits and decision envelopes as runtime-enforced workflow steps. | Approval is not a side-note; it blocks execution until resolved. | workflow approval tests |
| `BB-R2-WP04` | `A6` | `BB-R2-WP03` | Add incident lifecycle surfaces for pause, quarantine, rollback preview, and recovery. | Operator can handle one live incident end-to-end in UI and API without shell-only fallback. | UI acceptance plus route tests |
| `BB-R2-WP05` | `A2` | `BB-R2-WP04` | Add bounded self-heal and scheduler response under failure using queue health, node staleness, and retry policy. | Scheduler degrades safely under failure and exposes incident evidence. | chaos and recovery tests |
| `BB-R2-WP06` | `A5` | `BB-R2-WP03`, `BB-R2-WP04`, `BB-R2-WP05` | Add chaos, rollback, and incident acceptance gates. | `v4` governance claims are backed by repeatable failure drills. | nightly chaos, targeted release acceptance |

### 8.4 Track R3: V5 Tenant And Fleet Foundation

This track introduces hard boundaries and control-plane shape before economics.

| ID | Owner | Depends on | Action | Done means | Verify with |
|---|---|---|---|---|---|
| `BB-R3-WP01` | `A1` | `G5` | Define tenant model for organization, team, project, environment, and workflow scope. | Every governed object has one explicit scope chain. | schema tests and scope tests |
| `BB-R3-WP02` | `A4` | `BB-R3-WP01` | Add persistence and APIs for tenant and project identity, including correlation on tasks, workflows, events, and artifacts. | Core records can be filtered and enforced by tenant and project. | API tests, store tests, migration tests |
| `BB-R3-WP03` | `A3` | `BB-R3-WP01` | Implement governance inheritance from org to team to project to workflow with explicit override rules. | Inheritance is deterministic and conflicts fail closed. | governance tests |
| `BB-R3-WP04` | `A2` | `BB-R3-WP02` | Add fleet control plane views for worker pools, queue states, drain, rebalance, and environment-specific routing. | Multi-node operations are visible and controllable by scope. | multi-node tests, route tests |
| `BB-R3-WP05` | `A6` | `BB-R3-WP02`, `BB-R3-WP04` | Add tenant-aware control center views and scoped operator navigation. | Operators can view and act within scope without cross-scope leakage. | UI acceptance tests |
| `BB-R3-WP06` | `A5` | `BB-R3-WP02`, `BB-R3-WP03`, `BB-R3-WP04`, `BB-R3-WP05` | Add tenant-isolation and fleet-safety gates. | Cross-tenant bleed or unauthorized fleet actions fail acceptance. | multi-scope acceptance, security negatives |

### 8.5 Track R4: V5 Economics And GA Hardening

This track adds usage, quota, budget, and release-grade operational discipline.

| ID | Owner | Depends on | Action | Done means | Verify with |
|---|---|---|---|---|---|
| `BB-R4-WP01` | `A4` | `G6` | Add usage accounting on workflow, task, queue, and worker dimensions. | Runtime can attribute execution and resource usage to scopes. | accounting tests |
| `BB-R4-WP02` | `A3` | `BB-R4-WP01` | Add quota and budget enforcement with fail-closed policy decisions. | Execution is denied or degraded predictably when budgets are exceeded. | quota tests, policy tests |
| `BB-R4-WP03` | `A2` | `BB-R4-WP01` | Add cost-aware routing inputs and fleet scheduling behavior. | Scheduler chooses within policy, quota, and health boundaries. | routing and load tests |
| `BB-R4-WP04` | `A6` | `BB-R4-WP01`, `BB-R4-WP02`, `BB-R4-WP03` | Build portfolio command center views for throughput, quality, SLA risk, and quota burn. | Operators can see economics and operational risk in one place. | UI acceptance tests |
| `BB-R4-WP05` | `A5` | `BB-R4-WP02`, `BB-R4-WP03`, `BB-R4-WP04` | Add `v5` GA hardening: failover drills, release windows, evidence bundles, and promotion gates. | Release decision is supported by measurable evidence, not manual optimism. | release gates, failover drills, acceptance |

## 9. Sprintable Immediate Next Steps

These are the next ten smallest effective coding steps. They are ordered for immediate execution and can be split across the seven agents without write conflicts.

1. `NOW-01` create the contract canon artifact for CLI, FastAPI, stdlib, and MCP response shapes
2. `NOW-02` bind contract tests to that canon and fail on drift
3. `NOW-03` add queue lease schema hardening for uniqueness, indexing, and fencing token support
4. `NOW-04` add idempotency receipts for result reporting
5. `NOW-05` make `report_queue_lease_result` fully transactional under race conditions
6. `NOW-06` add dispatch event persistence and read support
7. `NOW-07` route handoff initiation through dispatch plus queue path behind a temporary feature flag
8. `NOW-08` add parent-child state sync and merge-on-report for queue-backed handoff
9. `NOW-09` promote workflow persistence schema from design artifact to runtime migration
10. `NOW-10` stabilize dashboard response segmentation and `contract_version` for operator-facing surfaces

## 10. Parallel Agent Work Allocation

The work should be executed in waves, not as seven independent roadmaps.

### 10.1 Wave V2.5-A

- `A1`: `BB-R0-WP01`
- `A2`: `BB-R0-WP03`
- `A3`: review security implications of result-auth and release isolation requirements
- `A4`: `BB-R0-WP02`
- `A5`: define acceptance for `G1` and `G3`
- `A6`: design read-only operability panels for dispatch, queue, and lease evidence
- `A7`: maintain dependency board and cut list

### 10.2 Wave V2.5-B

- `A2`: `BB-R0-WP04`
- `A4`: `BB-R0-WP05`, then `BB-R0-WP06`
- `A5`: end-to-end acceptance around queue-backed handoff
- `A6`: control center contract stabilization
- `A3`: provenance gate definition for isolated release tree
- `A7`: decide feature-flag removal timing

### 10.3 Wave V3

- `A1`: `BB-R1-WP01`
- `A3`: `BB-R1-WP02`
- `A4`: `BB-R1-WP03`
- `A6`: `BB-R1-WP04`
- `A2`: `BB-R1-WP05`
- `A5`: `BB-R1-WP06`
- `A7`: enforce `G4` cut decision before any `v4` work starts

### 10.4 Wave V4

- `A1`: `BB-R2-WP01`
- `A3`: `BB-R2-WP02`
- `A4`: `BB-R2-WP03`
- `A6`: `BB-R2-WP04`
- `A2`: `BB-R2-WP05`
- `A5`: `BB-R2-WP06`
- `A7`: protect scope from `v5` economics leakage until `G5` passes

### 10.5 Wave V5

- `A1`: `BB-R3-WP01`
- `A4`: `BB-R3-WP02`, `BB-R4-WP01`
- `A3`: `BB-R3-WP03`, `BB-R4-WP02`
- `A2`: `BB-R3-WP04`, `BB-R4-WP03`
- `A6`: `BB-R3-WP05`, `BB-R4-WP04`
- `A5`: `BB-R3-WP06`, `BB-R4-WP05`
- `A7`: manage alpha to GA cutover and no-go decisions

## 11. Control Instructions Per Agent

These are the operating instructions for the seven agent strands.

### 11.1 A1 Senior Software Architect

- produce ADRs before schema or contract expansion
- define one bounded context decision at a time
- reject feature work that spans multiple bounded contexts without an interface contract
- own all decisions that affect more than one execution agent

### 11.2 A2 Senior Distributed Systems Engineer

- maintain one source of truth for lease, queue, and worker state
- add fencing, idempotency, and recovery before adding throughput features
- prefer deterministic degradation over clever scheduling
- no remote transport promotion without authenticated result and recovery paths

### 11.3 A3 Senior Security Architect

- every remote or risky action must have explicit policy and fail-closed behavior
- approval is a runtime contract, not a documentation instruction
- packaging and provenance are part of security ownership
- no wildcard or bootstrap shortcuts survive version promotion

### 11.4 A4 Senior Backend Engineer

- keep TaskStore and gateway contracts explicit and additive
- persist workflow and policy before adding advanced reasoning surfaces
- ensure trace, history, task, and replay identities correlate cleanly
- schedule refactors only after behavior is fixed by tests

### 11.5 A5 Senior QA And Release Engineer

- build gates before optimism
- treat skipped critical tests as failures once a track reaches gate status
- keep one clean release tree path under constant verification
- add chaos and failure drills only after the basic path is deterministic

### 11.6 A6 Senior UI And UX Engineer

- operator UX must reveal queue, lease, policy, replay, incident, and quota state without hiding causal evidence
- add read-first surfaces before write-heavy controls
- control center contract versioning must stay stable
- no UI action that bypasses the enforced runtime policy path

### 11.7 A7 Principal Product And Systems Orchestrator

- maintain the dependency board and gate board daily
- cut speculative work immediately if it delays a gate
- keep version goals narrow and testable
- prevent `v6` or `v7` ambitions from contaminating `v2.5` to `v5`

## 12. Exit Criteria By Version

### 12.1 Exit For V2.5

- `G1`, `G2`, and `G3` all pass
- queue-backed handoff is canonical
- workflow and policy persistence exist in live runtime
- publishable release tree is isolated and verified

### 12.2 Exit For V3

- `G4` passes
- replay explains route and policy differences deterministically
- operator can inspect intervention queue and workflow evidence
- policy-aware routing is bounded and test-backed

### 12.3 Exit For V4

- `G5` passes
- incident lifecycle and rollback are productized
- approval circuits block risky execution correctly
- controlled autonomy exists without fail-open shortcuts

### 12.4 Exit For V5

- `G6` and `G7` pass
- tenant and project boundaries are enforced
- usage, quota, and budget logic are measurable and auditable
- operators can manage fleet, economics, and incidents in one governed control plane

## 13. Explicit Non-Goals Until V5 GA

These items are out of scope unless they directly unblock a listed work package:

- no `v6` distributed fabric platform buildout
- no `v7` agent operating substrate
- no internal marketplace or bidding economy
- no uncontrolled self-modifying execution
- no cloud-only rewrite
- no opaque policy-free ML routing for critical paths

## 14. Known Risks

- `tasks.py`, `evals.py`, `task_merge_service.py`, and `botboy_mcp_server.py` remain refactor pressure points
- repo isolation may expose hidden path assumptions in release acceptance
- workflow persistence can create migration complexity if identifiers are not frozen early
- tenant scoping will fail if correlation fields are added inconsistently across stores and routes
- UI can accidentally outrun back-end truth if operator surfaces are built before contract stabilization

## 15. Program Management Rules

- no new milestone starts until its gate owner and verifying owner are named
- every sprint must close at least one gate-relevant work package
- every release candidate must use the isolated publishable tree
- any feature flag introduced in `v2.5` must have a documented removal decision by the end of `v3`
- this file must be updated when a gate changes, a work package is split, or a version target is cut

## 16. Immediate Recommendation

The next implementation sequence should be:

1. `BB-R0-WP01`
2. `BB-R0-WP02`
3. `BB-R0-WP03`
4. `BB-R0-WP04`
5. `BB-R0-WP05`
6. `BB-R0-WP06`
7. `BB-R0-WP07`
8. `BB-R0-WP08`

Reason:

- this closes the real current blockers
- it converts the existing runtime from promising to canonical
- it creates the only safe foundation for `v3`, `v4`, and `v5`
