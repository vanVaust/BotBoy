---
name: expr
version: 1.0.0
description: >
  Sandboxed expression and mini-program evaluator: arithmetic, math and statistics functions,
  variables, lists, comprehensions and conditionals, interpreted from an AST allowlist under
  step, time, integer-size and sequence-length budgets. No eval/exec, no I/O.
triggers: [expr]
security_level: BEGINNER
needs_approval: false
allow_network: false
allow_filesystem: false
remote_policy: allow
cacheable: true
capabilities: [math, statistics, compute]
input_schema:
  type: object
  properties:
    program: {type: string, description: "Expression or ';'-separated assignments ending in an expression"}
  required: [program]
output_schema:
  type: object
  properties:
    result: {description: "Value of the last expression (JSON when small and plain)"}
    result_text: {type: string}
    variables: {type: object}
    elapsed_ms: {type: number}
layer:
  id: botboy.native.expr
  resources: {compute: low, memory: 16MB, storage: none, network: false}
  security:
    sandbox: inprocess
    permissions: []
    protection: AST node allowlist, no attribute access, 200k step and 1s budgets, 4096-bit integers, 10k-element sequences
  runtime:
    type: builtin
    entry: botboy.skills.native.expr:run
    timeout: 2
---

# expr

    expr 2**10 + sqrt(16)
    expr r = 0.05; n = 10; 1000 * (1 + r) ** n
    expr xs = [2, 4, 4, 5, 7]; mean(xs), stdev(xs), quantiles(xs, n=4)
    expr [x * x for x in range(10) if x % 2 == 0]

## Threat model

- Code execution: impossible by construction. Source is parsed, never compiled or
  executed; only allowlisted node types are interpreted. Attribute access (the
  usual `().__class__.__subclasses__()` escape), lambdas, imports, strings and
  every statement other than assignment are rejected before evaluation.
- Denial of service: integer results are capped at 4096 bits and `**`, `*`, `<<`
  are checked *before* computing; `factorial`, `comb`, `perm`, `range`,
  `quantiles`, `sum`, `prod` and `lcm` are bounded; every node costs a step
  (200k max) and a 1 s wall-clock deadline applies. Rendering is bounded too,
  so shared-reference structures cannot explode the output.
- Data access: none. The skill has no filesystem or network access and is
  therefore allowed over the gateway without approval.
