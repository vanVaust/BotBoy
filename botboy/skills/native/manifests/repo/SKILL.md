---
name: repo
version: 1.0.0
description: >
  Read-only git inspection of repositories inside the workspace: status, log, diff with
  numstat or patch, branches and commit details, run with a fixed argv and a scrubbed
  environment, refusing repositories whose config would make git execute programs.
triggers: [repo]
security_level: INTERMEDIATE
needs_approval: false
allow_network: false
allow_filesystem: true
remote_policy: approval
cacheable: false
capabilities: [filesystem, read-only, git, vcs]
input_schema:
  type: object
  properties:
    action: {type: string, enum: [status, log, diff, branches, show]}
    path: {type: string, description: "Repository directory relative to the workspace root"}
    rev: {type: string}
  required: [action]
output_schema:
  type: object
  properties:
    branch: {type: string}
    commits: {type: array}
    files: {type: array}
layer:
  id: botboy.native.repo
  resources: {compute: low, memory: 64MB, storage: workspace-read, network: false}
  security:
    sandbox: subprocess
    permissions: [fs.read.workspace, proc.spawn.git]
    protection: no shell, no system/global config, forced safe -c settings, config audit for executable keys, real .git directory required, validated revisions after --end-of-options, 10s timeout
  runtime:
    type: builtin
    entry: botboy.skills.native.repo:run
    timeout: 12
---

# repo

    repo status [path]
    repo log [path] -n 20 [--rev main]
    repo diff [path] [--staged] [--rev main..feature] [--patch]
    repo branches [path]
    repo show <rev> [path]

## Threat model

- Code execution through repository config: `core.fsmonitor`, `filter.*`,
  `diff.*.textconv`, `diff.external`, pagers, hooks and similar keys can make plain
  read commands run programs. The skill audits the effective local config first
  and refuses such repositories, and additionally forces safe values on the
  command line (`core.fsmonitor=false`, empty hooks path, `--no-ext-diff`,
  `--no-textconv`, submodules ignored).
- Escaping the workspace: `.git` must be a real directory inside the workspace;
  gitfiles and symlinks (worktrees, submodules pointing elsewhere) are refused and
  `--git-dir`/`--work-tree` are explicit, so no discovery walks upward.
- Option injection: revisions must match a conservative pattern, cannot start with
  `-`, and are passed after `--end-of-options`.
- Writes: `GIT_OPTIONAL_LOCKS=0` and `--no-optional-locks` keep `status` from
  rewriting the index; no mutating subcommand is reachable.
