# BotBoy Security & Execution Architecture — Master Build Plan

> **Status:** Living project source of truth  
> **Scope:** Security v5.1 foundation and the execution architecture required to make it auditable, testable, and releasable  
> **Repository:** `vanVaust/BotBoy`  
> **Primary branch:** `security/v5.1-foundation`  
> **Last plan revision:** 2026-09-25  
>
> This document is intentionally maintained **inside the repository**. It is not a retrospective report. It is the authoritative engineering blueprint for completing, consolidating, testing, and releasing the security/execution foundation. When implementation changes the architecture, this document must be updated in the same change set or immediately afterward.

---

## 0. Executive Definition

BotBoy is an agent runtime with multiple execution transports, persistent tasks, queues, workers, recovery, replay, MCP, gateways, artifacts, and observability.

The security problem is therefore not simply:

> "Does this HTTP endpoint check authentication?"

The actual security property is:

> **No security-relevant effect may occur unless the current execution is authorized for the exact principal, tenant, object/task, capability, command/effect, and authorization version applicable at the moment of effect.**

The system must preserve that property across **all execution boundaries**, including:

```
Principal
  -> SecurityContext
  -> Authorization
  -> Approval Grant
  -> Task
  -> Queue / Lease
  -> Worker
  -> Recovery / Resume / Retry
  -> Final Authorization
  -> Effect / Execution
```

The same principle applies to read-side access:

```
Principal + Tenant + Object relationship + Capability
  -> scoped read
```

### Release objective

Security v5.1 is complete only when:

1. the execution authorization invariant is implemented centrally;
2. every security-sensitive execution path reaches that authorization boundary;
3. approvals are server-issued, exact-scope, expiring, single-use grants;
4. recovery and retries cannot inherit stale authorization implicitly;
5. all read/write surfaces are principal/tenant scoped;
6. artifact and worker/queue access obey the same object-level model;
7. adversarial tests prove the important bypass classes fail closed;
8. release CI makes security regressions release-blocking;
9. the architecture is consolidated enough to be reviewed and maintained;
10. the repository documents the resulting contracts.

---

# 1. Current State Model

## 1.1 Existing security building blocks

The current branch already contains substantial foundations:

- immutable `SecurityContext`;
- centralized authorization decisions;
- capability/scoping concepts;
- task-persisted security state;
- server-side `ApprovalStore`;
- task/worker/queue security adapters;
- recovery security;
- merge security;
- read-side gateway scoping;
- cache/security-scope work;
- final execution authorization gate;
- fail-closed remote gateway/auth behavior;
- release build/install acceptance;
- broad regression coverage.

These are **foundations, not proof of completion**.

## 1.2 Current architectural risk

The branch has grown through many incremental security patches. That has created several risks:

- multiple sources of identity/context;
- import-time monkeypatching;
- security code reaching into private store internals;
- compatibility fallbacks that can obscure the intended contract;
- duplicated authorization logic at adapters;
- divergent gateway and execution paths;
- insufficiently explicit contracts between task, worker, queue, recovery, and execution layers.

The next phase is therefore **consolidation and proof**, not feature accumulation.

---

# 2. Security Classification

Every BotBoy operation must be classified before implementation.

## Category A — Identity / Principal

Defines **who** is acting.

Examples:

- authenticated user;
- service principal;
- worker principal;
- scheduler/system principal;
- MCP caller.

Required properties:

- stable principal identifier;
- explicit authentication source;
- no implicit privilege from missing identity;
- no accidental use of user-controlled identity fields as authoritative identity.

### Invariant A1

An unauthenticated or `anonymous` principal cannot authorize a security-sensitive effect.

---

## Category B — Tenant / Organization Boundary

Defines **which security domain** the operation belongs to.

Required properties:

- every persistent security-sensitive object has an effective `org_id`;
- cross-tenant access is denied by default;
- tenant identity comes from authoritative security state, not arbitrary request payload;
- system principals are explicit and auditable.

### Invariant B1

A principal authorized in tenant A cannot use the same authorization state to access tenant B.

---

## Category C — Object / Task Authorization

Defines **what concrete object** the principal may access.

Objects include:

- task;
- child task;
- artifact;
- queue;
- lease;
- worker node;
- merge review;
- trace;
- replay;
- memory entry;
- delegation state.

### Invariant C1

Object ownership or permitted relationship must be checked before exposing or mutating object state.

---

## Category D — Capability

Defines **what class of operation** is allowed.

Examples:

- `task.read`;
- `task.resume`;
- `task.cancel`;
- `task.execute`;
- `worker.register`;
- `worker.heartbeat`;
- `worker.lease`.

Capabilities are not approvals.

### Invariant D1

Possessing a capability does not automatically authorize a particular object or command.

---

## Category E — Approval

Defines a **specific server-issued authorization grant** for a sensitive effect.

Approval must be bound to:

- approval ID;
- task ID;
- principal;
- tenant;
- capability;
- exact command/effect fingerprint;
- authorization version;
- issue time;
- expiry;
- consumption state.

### Invariant E1

Client-provided values such as `approval=true`, `granted=true`, or free-form approval metadata are never authoritative.

### Invariant E2

A consumed approval cannot authorize a second execution.

---

## Category F — Authorization Version

Defines the security policy state against which the grant was issued.

Authorization version must invalidate stale approvals when security-relevant policy changes.

### Invariant F1

An approval issued under an obsolete authorization version cannot authorize execution under a newer version.

---

## Category G — Execution Boundary

Defines the last point before a security-relevant effect occurs.

The final gate must execute **immediately before the effect**.

### Invariant G1

No alternate route may reach the effect without passing the final authorization contract.

---

## Category H — Recovery / Resume / Retry

Defines re-entry into execution after interruption.

Recovery is a new security decision, not a continuation of historical trust.

### Invariant H1

A persisted task is not proof of current authorization.

---

## Category I — Worker / Queue / Lease

Defines distributed execution ownership.

Security must bind:

- worker;
- node;
- queue;
- lease;
- task;
- tenant;
- principal;
- lease state.

### Invariant I1

A worker cannot execute a task merely because it possesses a queue lease; task authorization remains independently required.

---

## Category J — Read / Observability

Defines who may see state.

Includes:

- task summaries;
- history;
- traces;
- dashboard;
- metrics;
- worker state;
- queues;
- reflection;
- delegation;
- A2A;
- replay;
- artifacts.

### Invariant J1

Observability is data access and therefore requires authorization/scoping.

---

## Category K — Artifact / Filesystem

Defines access to generated or persisted files.

### Invariant K1

Artifact authorization follows the security context of the owning task/object; filenames or paths are not sufficient authorization.

---

## Category L — Runtime Sandbox

Defines what the process can actually do at the operating-system boundary.

Authorization answers "may this principal request the effect?"  
Sandboxing answers "what can the process technically perform?"

These must not be conflated.

---

# 3. Canonical Security Model

The target model is:

```
Authenticated Principal
        |
        v
Canonical SecurityContext
        |
        v
AuthorizationDecision
        |
        +----> object/task binding
        |
        +----> capability binding
        |
        +----> tenant binding
        |
        +----> authorization version
        |
        v
ApprovalStore (when required)
        |
        v
Task / Queue / Worker boundary
        |
        v
Final Authorization
        |
        v
Execution Effect
```

## 3.1 Canonical SecurityContext

`SecurityContext` is the authoritative security identity carried across execution boundaries.

It may contain:

- principal;
- organization;
- roles;
- scopes;
- capabilities;
- authentication source;
- session;
- request;
- task;
- parent task;
- approval;
- approval scope;
- approval expiry;
- authorization version.

### Rule

Downstream components may **reduce** authority but must never silently increase it.

Child/delegated context:

```
child.capabilities ⊆ parent.capabilities
child.approval_scope ⊆ parent.approval_scope
```

---

# 4. Canonical Authorization Contract

The project must converge on one authoritative execution authorization service/API.

Conceptually:

```text
authorize_execution(
    security_context,
    task,
    command,
    required_capability,
    approval_id?,
    now?
) -> AuthorizationDecision
```

The implementation name may differ, but the contract must provide the following checks.

## 4.1 Required checks

1. principal exists and is authoritative;
2. principal matches the persisted task security state;
3. tenant matches;
4. task matches;
5. capability is granted;
6. command/effect matches the authorization target;
7. authorization version matches;
8. approval is required when policy says so;
9. approval exists;
10. approval is bound to the same principal;
11. approval is bound to the same tenant;
12. approval is bound to the same task;
13. approval is bound to the same capability;
14. approval is bound to the exact command/effect;
15. approval has not expired;
16. approval is consumed exactly once;
17. the authorization result is evaluated at the current execution boundary.

## 4.2 Deny-by-default

Unknown, missing, malformed, stale, contradictory, or unavailable security state must result in denial for security-sensitive operations.

The system must not silently substitute:

- anonymous;
- local development identity;
- in-memory approval;
- task payload identity;
- client-supplied roles;
- stale persisted authorization;
- worker identity alone.

---

# 5. Approval Protocol

The required lifecycle is:

```
1. policy determines approval is required
2. server evaluates requester
3. server issues approval grant
4. grant is persisted
5. execution path carries approval reference
6. final boundary validates exact binding
7. grant is atomically consumed
8. execution occurs
```

## 5.1 Important ordering

The implementation must prevent this unsafe pattern:

```
consume
  -> unrelated processing
  -> authorization state changes
  -> execution
```

The final design should make the consumed grant and final authorization decision part of one tightly controlled boundary.

## 5.2 Replay

These must all fail:

- replaying the same approval ID;
- changing the command;
- changing the task;
- changing the principal;
- changing the tenant;
- changing the capability;
- using the approval after expiry;
- using the approval after authorization-version change.

---

# 6. Execution Boundary Architecture

Every route into an effect must converge on the same security core.

## 6.1 External boundaries

Required:

- FastAPI;
- stdlib gateway;
- WebSocket;
- MCP;
- CLI where it can initiate sensitive execution.

## 6.2 Internal boundaries

Required:

- command router;
- command execution service;
- task resume;
- retry;
- recovery;
- scheduler;
- queue;
- worker;
- worker handoff;
- delegation;
- merge actions;
- subprocess/effect adapters.

## 6.3 Boundary rule

Adapters may perform contextual validation, but they must not create an alternative security model.

Preferred:

```
adapter
  -> canonical context
  -> canonical authorization
  -> domain operation
```

Avoid:

```
adapter A -> custom authorization
adapter B -> custom authorization
adapter C -> custom authorization
execution -> implicit trust
```

---

# 7. Final Execution Gate

## Objective

The final gate is the last security barrier before an irreversible or security-relevant effect.

## Required properties

- fail closed;
- no client-controlled authorization;
- no private DB implementation dependency;
- no silent compatibility bypass;
- no alternate execution path;
- deterministic failure reason;
- auditable decision;
- tested directly and through the real execution service.

## Current implementation debt

The current final gate contains architectural compromises that must be removed during consolidation:

- import-time monkeypatching of `CommandExecutionService._run_route`;
- direct use of private `TaskStore._get_conn()`;
- compatibility return paths for stores without the private connection API;
- duplicated approval-row validation outside the approval abstraction.

These may be useful as temporary diagnostics, but they are not the target architecture.

## Definition of Done

The final gate is complete only when:

1. it uses a supported security/approval API;
2. no private storage implementation is required;
3. all real execution paths pass it;
4. missing approval fails;
5. fake approval fails;
6. wrong task fails;
7. wrong principal fails;
8. wrong tenant fails;
9. wrong capability fails;
10. wrong command fails;
11. expired approval fails;
12. consumed approval fails;
13. stale authorization version fails;
14. successful valid execution passes;
15. CI proves the complete contract.

---

# 8. Recovery / Resume / Retry

Recovery must create or re-establish a current execution security context.

## Required checks

For every resume/retry/recovery:

- current principal;
- current tenant;
- persisted task security state;
- current authorization version;
- current capability;
- current task status;
- approval requirements;
- approval validity;
- worker authorization;
- lease validity.

## Explicit anti-pattern

Never implement:

```text
persisted_task.approved == true
        -> execute()
```

Instead:

```text
persisted_task
  -> reconstruct security state
  -> current authorization
  -> current approval
  -> final execution authorization
  -> execute
```

---

# 9. Worker / Queue / Lease Security

Worker infrastructure is part of the security boundary.

## Worker identity

A worker must have an explicit identity.

## Queue authorization

A worker must only receive work it is authorized to process.

## Lease authorization

A lease is an execution coordination mechanism, not proof of task authorization.

Required binding:

```
lease
  -> queue
  -> worker
  -> task
  -> tenant
```

## Recovery

Expired/stale leases must not become a privilege escalation path.

---

# 10. Read-Side Security

Every read must answer:

> Who is requesting this data, in which tenant, and why may they see this object?

## Protected surfaces

- tasks;
- task graph;
- history;
- traces;
- metrics;
- dashboard;
- worker nodes;
- execution queues;
- queue leases;
- reflection;
- delegation;
- A2A;
- replay;
- artifacts;
- merge review;
- memory where applicable.

## Aggregates

Global aggregates are dangerous because they can leak:

- counts;
- tenant existence;
- worker identities;
- task activity;
- operational load;
- timing;
- other users' behavior.

Authenticated non-system users must receive only scoped aggregates.

---

# 11. Artifact Security

Artifacts must be authorized through object ownership/relationship.

## Required model

```
artifact
  -> owning task/object
  -> task SecurityContext
  -> principal + tenant authorization
```

## Required tests

- foreign tenant;
- foreign principal;
- foreign task;
- nonexistent task;
- deleted task;
- path traversal;
- absolute path;
- symlink/alias escape where applicable;
- artifact enumeration;
- worker-produced artifact;
- replay/evaluation artifact.

---

# 12. Sandbox / OS Boundary

Authorization is not a sandbox.

For sensitive capabilities, document and enforce the actual runtime boundary for:

- subprocess;
- filesystem;
- network;
- environment;
- secrets;
- working directory;
- artifact directory;
- process spawning.

## Required product distinction

BotBoy must explicitly distinguish:

### Local trusted mode

Convenience-oriented local runtime with documented trust assumptions.

### Sandboxed mode

Execution under explicit OS/runtime restrictions.

The project must never imply that a Python authorization check alone provides OS-level isolation.

---

# 13. Adversarial Security Test Matrix

This matrix is a release requirement.

## Identity

- missing principal -> DENY
- anonymous -> DENY
- principal mismatch -> DENY
- task principal changed -> DENY
- approval principal changed -> DENY
- worker impersonation -> DENY

## Tenant

- wrong tenant -> DENY
- task/approval tenant mismatch -> DENY
- queue/task tenant mismatch -> DENY
- worker/task tenant mismatch -> DENY
- cross-tenant cache access -> DENY

## Task

- wrong task -> DENY
- unrelated child task -> DENY
- foreign artifact -> DENY
- foreign merge task -> DENY

## Capability

- missing capability -> DENY
- child capability escalation -> DENY
- worker capability escalation -> DENY
- approval capability mismatch -> DENY

## Approval

- missing -> DENY
- fake -> DENY
- expired -> DENY
- consumed -> DENY
- replay -> DENY
- wrong command -> DENY
- wrong task -> DENY
- wrong principal -> DENY
- wrong tenant -> DENY
- wrong capability -> DENY
- stale authorization version -> DENY

## Execution

- direct internal route bypass -> DENY
- alternate gateway -> DENY
- MCP bypass -> DENY
- WebSocket bypass -> DENY
- scheduler bypass -> DENY
- worker bypass -> DENY
- recovery bypass -> DENY
- retry bypass -> DENY

## Data

- global summary leak -> DENY
- foreign task read -> DENY
- foreign worker read -> DENY
- foreign queue read -> DENY
- foreign trace read -> DENY
- foreign artifact read -> DENY

---

# 14. Observability and Audit

Security failures must be diagnosable without leaking protected data.

## Every security decision should make it possible to identify

- request ID;
- task ID;
- principal;
- tenant;
- capability;
- authorization version;
- decision;
- failure category;
- approval ID where applicable.

Do not log:

- secrets;
- raw credentials;
- full tokens;
- unnecessary command payloads containing sensitive information.

## Required failure classes

Prefer stable machine-readable categories such as:

- `missing_principal`;
- `principal_mismatch`;
- `org_mismatch`;
- `task_mismatch`;
- `capability_missing`;
- `approval_missing`;
- `approval_invalid`;
- `approval_expired`;
- `approval_consumed`;
- `authorization_version_mismatch`;
- `worker_not_authorized`;
- `object_not_visible`.

---

# 15. CI / Release Gates

Security must become a release gate.

Required pipeline:

```
Build
  ->
Clean Install
  ->
Release Acceptance
  ->
Full Unit/Integration Suite
  ->
Security Regression Suite
  ->
Adversarial Boundary Suite
  ->
Packaging Verification
```

## Release must fail if

- any security regression fails;
- any adversarial case unexpectedly succeeds;
- build/install differs from source-tree behavior;
- a security-sensitive path bypasses the canonical authorization service;
- approval persistence is unavailable and the operation would otherwise continue;
- security state is ambiguous.

---

# 16. Repository / Architecture Consolidation

The security branch has accumulated many incremental commits. Before declaring v5.1 complete:

1. identify duplicate authorization implementations;
2. identify monkeypatch-based security hooks;
3. identify private-store access from security adapters;
4. identify compatibility bypasses;
5. centralize canonical contracts;
6. simplify adapters;
7. update tests to target contracts rather than implementation details;
8. integrate current `main`;
9. run full acceptance again.

## Rule

Security code must be **boring to call**:

```
context
  -> authorize
  -> execute
```

The complexity belongs in the centralized security implementation, not distributed across every route.

---

# 17. Work Breakdown Structure

## P0 — Release blockers

### P0.1 Final execution authorization
- repair failing CI tests;
- remove fail-open compatibility branches;
- replace private DB access;
- centralize approval verification;
- verify actual execution boundary.

### P0.2 Canonical authorization service
- define stable API;
- make it the single execution authority;
- migrate adapters;
- remove duplicated authorization logic.

### P0.3 Approval lifecycle
- issue;
- persist;
- validate;
- consume atomically;
- bind exact command/task/principal/tenant/capability/version;
- replay prevention.

### P0.4 Recovery closure
- resume;
- retry;
- stale worker;
- stale lease;
- recovery;
- authorization version changes.

### P0.5 Entry-point closure
- FastAPI;
- stdlib;
- WebSocket;
- MCP;
- CLI;
- scheduler;
- workers;
- handoffs.

### P0.6 Adversarial suite
- identity;
- tenant;
- task;
- capability;
- approval;
- replay;
- recovery;
- transport;
- data.

---

## P1 — Security completeness

### P1.1 Read-side closure
Complete object-level and tenant-level scoping.

### P1.2 Artifact closure
Complete artifact ownership and filesystem boundary.

### P1.3 Sandbox boundary
Document and implement actual OS/runtime restrictions.

### P1.4 Auditability
Stable decision categories and security-safe observability.

### P1.5 CI enforcement
Security suites are release-blocking.

### P1.6 Branch consolidation
Reduce implementation duplication and review complexity.

---

## P2 — Post-v5.1 hardening

Only after P0/P1:

- formal policy engine evolution;
- richer capability taxonomy;
- stronger cryptographic approval tokens if needed;
- external policy providers;
- remote worker trust model;
- hardened sandbox backends;
- formal threat-model documentation;
- performance optimization;
- additional agent features.

---

# 18. Definition of Done

Security v5.1 is **DONE** only when all statements below are true.

## Architecture

- [ ] one canonical SecurityContext;
- [ ] one canonical execution authorization service;
- [ ] one canonical approval store/protocol;
- [ ] no execution bypass;
- [ ] no hidden privilege escalation path.

## Identity / Tenant

- [ ] principal is authoritative;
- [ ] tenant is authoritative;
- [ ] cross-tenant access is denied;
- [ ] anonymous cannot authorize sensitive operations.

## Approval

- [ ] server-issued;
- [ ] exact task binding;
- [ ] exact principal binding;
- [ ] exact tenant binding;
- [ ] exact capability binding;
- [ ] exact command/effect binding;
- [ ] authorization-version binding;
- [ ] expiry enforced;
- [ ] single-use enforced;
- [ ] replay denied.

## Execution

- [ ] final gate is real, not merely test instrumentation;
- [ ] every sensitive execution route reaches it;
- [ ] direct/internal bypass is impossible or denied;
- [ ] worker/queue execution remains authorized;
- [ ] scheduler execution remains authorized.

## Recovery

- [ ] resume re-authorizes;
- [ ] retry re-authorizes;
- [ ] recovery re-authorizes;
- [ ] stale approval cannot be reused;
- [ ] stale worker/lease cannot elevate authority.

## Read surfaces

- [ ] task data scoped;
- [ ] trace data scoped;
- [ ] worker data scoped;
- [ ] queue data scoped;
- [ ] dashboard scoped;
- [ ] metrics scoped;
- [ ] artifacts scoped;
- [ ] reflection/delegation/A2A scoped.

## Runtime

- [ ] sandbox assumptions documented;
- [ ] subprocess boundary defined;
- [ ] filesystem boundary defined;
- [ ] network boundary defined;
- [ ] secret boundary defined.

## Verification

- [ ] full test suite green;
- [ ] adversarial suite green;
- [ ] release acceptance green;
- [ ] clean-install acceptance green;
- [ ] CI security gates green;
- [ ] no known security bypass remains undocumented.

---

# 19. Change-Control Rules for This Document

This file is a **living source of truth**.

Whenever a security architecture change is made:

1. update the relevant section of this document;
2. update the affected tests;
3. update the Definition of Done if the contract changed;
4. record architectural deviations explicitly;
5. do not mark an item complete merely because code exists;
6. mark complete only when the behavior is tested and the relevant release gate passes.

## Status vocabulary

Use only:

- **PLANNED** — defined but not implemented;
- **IN PROGRESS** — implementation underway;
- **BLOCKED** — cannot proceed because of a concrete dependency;
- **IMPLEMENTED** — code exists;
- **VERIFIED** — implementation is covered by appropriate tests;
- **RELEASED** — verified and included in the accepted release baseline.

Never use "done" as a synonym for "code exists".

---

# 20. Current Execution Order

The implementation order is intentionally strict:

```
1.  Final execution gate
2.  Canonical authorization service
3.  Approval lifecycle
4.  Recovery / resume / retry
5.  Worker / queue / lease closure
6.  FastAPI / stdlib / WebSocket / MCP convergence
7.  Read-side closure
8.  Artifact closure
9.  Sandbox boundary
10. Adversarial security matrix
11. CI security gate
12. Branch consolidation
13. Full release acceptance
14. Only then: additional product capabilities
```

## Why this order

Each later layer depends on the earlier security contract.

Adding features before the contract is stable increases:

- attack surface;
- duplicate logic;
- test complexity;
- integration risk;
- review burden;
- probability of bypass.

The objective is therefore not maximum feature count.

The objective is a **closed security execution system whose authorization invariant survives every route through the runtime**.

---

# 21. Final Engineering Principle

The project should converge on one simple statement:

> **Identity is established once, authority is derived centrally, authority can only be reduced downstream, approvals are exact and single-use, persisted state is not current authorization, every effect reaches a final authorization boundary, and every read is scoped to the same security context.**

If a new feature cannot explain how it satisfies that statement, it is not ready to enter the execution surface.

