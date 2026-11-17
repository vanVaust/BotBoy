---
name: ws
version: 1.0.0
description: >
  Read-only workspace file tools: list, read with line ranges, grep (literal or regex in an
  isolated worker), stat with sha256, text statistics, keywords, summaries, RAG chunking
  and diffs, confined to the operator-configured workspace root.
triggers: [ws]
security_level: INTERMEDIATE
needs_approval: false
allow_network: false
allow_filesystem: true
remote_policy: approval
cacheable: false
capabilities: [filesystem, read-only, search, text-analysis]
input_schema:
  type: object
  properties:
    action: {type: string, enum: [list, read, grep, stat, stats, keywords, summarize, chunk, diff]}
    path: {type: string, description: "Relative to the workspace root"}
  required: [action]
output_schema:
  type: object
  properties:
    path: {type: string}
    entries: {type: array}
    matches: {type: array}
    chunks: {type: array}
layer:
  id: botboy.native.ws
  resources: {compute: medium, memory: 64MB, storage: workspace-read, network: false}
  security:
    sandbox: inprocess
    permissions: [fs.read.workspace, proc.spawn.python-isolated]
    protection: workspace confinement, no symlinks or hidden paths, O_NOFOLLOW opens, 2MB reads, 12MB grep budget
  runtime:
    type: builtin
    entry: botboy.skills.native.files:run
    timeout: 10
---

# ws

    ws list [dir] [--depth N]
    ws read <file> [--lines A:B] [--max-chars N]
    ws grep <pattern> [dir|file] [--glob '*.md'] [--ignore-case] [--fixed] [--max N]
    ws stat <path>
    ws stats|keywords|summarize|chunk <file>
    ws diff <file-a> <file-b>

## Threat model

- Path traversal and escapes: paths are relative to `skills.workspace_root`
  (`BOTBOY_WORKSPACE`); absolute paths, drive letters, `~`, `..`, hidden components
  and symlinks/junctions on any component are refused; files are opened with
  `O_NOFOLLOW` and re-checked with `fstat`.
- Secret exposure: dot-files and dot-directories (`.env`, `.git`, `.ssh`) are not
  reachable through this skill.
- Resource use: reads are capped at 2 MB per file, grep at 2000 files / 12 MB, output
  at 8000 characters; regex grep runs in the killable isolated worker.
- Writes: none. The skill never creates, modifies or deletes files.
- Remote use requires a server-issued approval (remote_policy: approval); the local
  operator, who chose the workspace, may run it directly. Results are never cached
  because files change.
