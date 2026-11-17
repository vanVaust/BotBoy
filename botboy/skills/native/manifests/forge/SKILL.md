---
name: forge
version: 1.0.0
description: >
  Skill forge: lint SKILL.md / LDF manifests against BotBoy's real loaders and runtime policy,
  audit every discovered custom skill, and scaffold new runnable skills that pass the lint.
triggers: [forge]
security_level: INTERMEDIATE
needs_approval: false
allow_network: false
allow_filesystem: true
remote_policy: approval
cacheable: false
capabilities: [filesystem, read-only, skill-authoring, lint]
input_schema:
  type: object
  properties:
    action: {type: string, enum: [lint, audit, scaffold]}
    path: {type: string, description: "SKILL.md or directory relative to the workspace root"}
  required: [action]
output_schema:
  type: object
  properties:
    reports: {type: array}
    errors: {type: integer}
    warnings: {type: integer}
    skill_md: {type: string}
layer:
  id: botboy.native.forge
  resources: {compute: low, memory: 32MB, storage: workspace-read, network: false}
  security:
    sandbox: inprocess
    permissions: [fs.read.workspace, fs.read.skills-dir]
    protection: parses manifests and code with yaml.safe_load and ast.parse only; never imports or executes skill code; scaffold returns text and writes nothing
  runtime:
    type: builtin
    entry: botboy.skills.native.forge:run
    timeout: 10
---

# forge

    forge lint skills/my-skill/SKILL.md
    forge lint skills
    forge audit
    forge scaffold word-counter --trigger wc --description "Count words in the given text"

## Threat model

- Reading: `lint` reads SKILL.md files inside the workspace; `audit` re-reads the
  SKILL.md files BotBoy already discovered in its configured skills directory.
- Execution: manifests are parsed with `yaml.safe_load`, code blocks only with
  `ast.parse` and the runtime's own static blocklist; nothing is imported or run.
- Writing: none; `scaffold` returns the manifest text for the operator to save.
