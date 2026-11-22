---
name: sqlite
version: 1.0.0
description: >
  Read-only SQL over SQLite databases in the workspace: list tables with row counts, show a
  table's columns, keys and indexes, and run one SELECT/WITH/VALUES statement with row, time and
  memory budgets in an isolated, killable worker process.
triggers: [sqlite]
security_level: INTERMEDIATE
needs_approval: false
allow_network: false
allow_filesystem: true
remote_policy: approval
cacheable: false
capabilities: [filesystem, read-only, data-analysis, query, sql]
input_schema:
  type: object
  properties:
    action: {type: string, enum: [tables, schema, query]}
    file: {type: string, description: "SQLite database relative to the workspace root"}
    table: {type: string, description: "schema: table or view name"}
    sql: {type: string, description: "query: one SELECT/WITH/VALUES statement"}
    max_rows: {type: integer, minimum: 1, maximum: 1000}
    format: {type: string, enum: [table, json, csv]}
  required: [action, file]
output_schema:
  type: object
  properties:
    columns: {type: array}
    rows: {type: array}
    more: {type: boolean}
    incomplete: {type: boolean}
    tables: {type: array}
layer:
  id: botboy.native.sqlite
  resources: {compute: medium, memory: 256MB, storage: workspace-read, network: false}
  security:
    sandbox: subprocess
    permissions: [fs.read.workspace]
    protection: pinned-descriptor snapshot opened mode=ro&immutable=1, SELECT-only lexical gate, authorizer with pure-function allowlist (no ATTACH/PRAGMA/VACUUM/load_extension), query_only, isolated python -I -S worker with 5s kill, VM-step budget, 256MB SQLite heap limit, rlimits where available, 256MB file cap, row/cell/output caps
  runtime:
    type: builtin
    entry: botboy.skills.native.sqlitedb:run
    timeout: 20
---

# sqlite

    sqlite tables shop.db
    sqlite schema shop.db orders
    sqlite schema shop.db
    sqlite query shop.db SELECT region, count(*), sum(amount) FROM orders GROUP BY region ORDER BY 3 DESC
    sqlite query --max-rows 200 --format csv shop.db SELECT * FROM orders WHERE amount > 100

## Threat model

- The database file is untrusted input. It is resolved through the workspace layer
  (no absolute paths, `..`, hidden components or symlinks) and copied through a
  descriptor opened beneath the pinned workspace root into a private temporary
  snapshot; SQLite only opens that copy, read-only and immutable, so a path swapped
  after the check reaches nothing and the original is never locked or modified.
  WAL-mode files are read from the main file; a non-empty `-wal` sibling is reported.
- Writes and side effects: only statements whose first keyword is SELECT, WITH or
  VALUES pass the gate. The authorizer then allows column reads, SELECT, recursive
  CTEs and a fixed list of pure functions; ATTACH (and with it `VACUUM INTO`, which
  can write a file even on a read-only connection), PRAGMA, DDL, DML, transactions
  and `load_extension` are denied, and the connection is `query_only`.
- Resource exhaustion: the query runs in a separate `python -I -S` process. A
  VM-step budget and a deadline stop long queries between operations; a hard SQLite
  heap limit turns string-doubling and huge-blob tricks into "out of memory"; on
  Python 3.11+ per-value length, expression depth, compound-select and attach limits
  are set too; CPU and address-space rlimits apply where the OS has them; and the
  parent kills the worker after 7 s, which also bounds single long C operations
  (`LIKE`, `instr`) that no in-process budget can interrupt. A crash of SQLite on a
  malformed file ends only the worker.
- Output: at most 1000 rows (default 50), 2000 characters per cell and 200k
  characters per response; integers stay exact and floats keep their full `repr`;
  control and bidi characters in names and cells are escaped in the text output.
- Remote use requires a server-issued approval (`remote_policy: approval`); results
  are never cached.

## Limitations

- Databases over 256 MB are refused (copying is O(size) per command).
- A file written while it is being copied can yield an inconsistent snapshot; SQLite
  then reports it as malformed.
- Virtual tables need their module (FTS3/4/5, R-Tree are built in; others fail).
