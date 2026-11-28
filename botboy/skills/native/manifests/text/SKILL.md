---
name: text
version: 1.0.0
description: >
  Analytics on inline text: statistics and readability, RAKE keyword extraction, extractive
  summaries, word/line diffs, and regular-expression testing in a killable isolated worker.
triggers: [text]
security_level: BEGINNER
needs_approval: false
allow_network: false
allow_filesystem: false
remote_policy: allow
cacheable: true
capabilities: [text-analysis, nlp, diff, regex]
input_schema:
  type: object
  properties:
    action: {type: string, enum: [stats, keywords, summarize, diff, regex]}
    text: {type: string}
  required: [action, text]
output_schema:
  type: object
  properties:
    action: {type: string}
    stats: {type: object}
    keywords: {type: array}
    sentences: {type: array}
    matches: {type: array}
layer:
  id: botboy.native.text
  resources: {compute: low, memory: 512MB, storage: none, network: false}
  security:
    sandbox: inprocess
    permissions: [proc.spawn.python-isolated]
    protection: regex runs in python -I -S child with rlimits and a 2s kill timeout
  runtime:
    type: builtin
    entry: botboy.skills.native.text:run
    timeout: 3
---

# text

    text stats <text>
    text keywords [--top N] <text>
    text summarize [--sentences N] <text>
    text diff <old> ||| <new>
    text regex [--flags imsxa] <pattern> ||| <subject>

## Threat model

- Input is the command's own text (at most 4096 characters after validation); there
  is no file or network access, so the skill is allowed over the gateway.
- ReDoS: Python's `re` has no timeout. Patterns run in a fresh `python -I -S`
  process that receives data only on stdin, has CPU and address-space rlimits
  where the OS supports them, and is killed after 2 s.
- All other algorithms (RAKE, Luhn-style summarisation, difflib) are linear or
  bounded by the input size.
