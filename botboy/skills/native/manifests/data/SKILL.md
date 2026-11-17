---
name: data
version: 1.0.0
description: >
  Query CSV, TSV, JSON and JSONL files in the workspace: schema inference, head, column
  statistics, and filter/select/group/aggregate/sort/limit queries rendered as table, JSON or CSV.
triggers: [data]
security_level: INTERMEDIATE
needs_approval: false
allow_network: false
allow_filesystem: true
remote_policy: approval
cacheable: false
capabilities: [filesystem, read-only, data-analysis, query]
input_schema:
  type: object
  properties:
    action: {type: string, enum: [schema, head, stats, query]}
    file: {type: string, description: "Relative to the workspace root"}
    query: {type: string, description: "where/select/group by/agg/sort/limit/format clauses"}
  required: [action, file]
output_schema:
  type: object
  properties:
    columns: {type: array}
    rows: {type: array}
    matched: {type: integer}
layer:
  id: botboy.native.data
  resources: {compute: medium, memory: 256MB, storage: workspace-read, network: false}
  security:
    sandbox: inprocess
    permissions: [fs.read.workspace]
    protection: workspace confinement, 20MB file cap, 200k row cap, declarative query language without eval
  runtime:
    type: builtin
    entry: botboy.skills.native.data:run
    timeout: 15
---

# data

    data schema sales.csv
    data head orders.json 5
    data stats sales.csv amount
    data query sales.csv where region == EU and amount > 100 select date,amount sort amount desc limit 10
    data query sales.csv group by region agg count sum:amount avg:amount sort sum_amount desc

## Threat model

- Injection: the query language is parsed into a small AST of conditions,
  projections and aggregates; no SQL engine, no `eval`, no regex operator.
- Confinement: files are resolved through the same workspace layer as `ws`.
- Resource use: files over 20 MB are refused (not truncated, to avoid silently
  wrong aggregates), at most 200k rows are loaded, output is capped and parsing runs
  off the event loop.
- Remote use requires a server-issued approval; results are never cached.
