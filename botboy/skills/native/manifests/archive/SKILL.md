---
name: archive
version: 1.0.0
description: >
  Inspect zip and tar(.gz/.bz2/.xz) archives in the workspace without extracting them: list
  entries with a safety verdict (path traversal, absolute paths, escaping links, devices, bombs,
  overlapping zip entries), verify every member under a decompression budget, and read one text
  member. Nothing is written to disk.
triggers: [archive]
security_level: INTERMEDIATE
needs_approval: false
allow_network: false
allow_filesystem: true
remote_policy: approval
cacheable: false
capabilities: [filesystem, read-only, archive, security-analysis]
input_schema:
  type: object
  properties:
    action: {type: string, enum: [list, check, read]}
    file: {type: string, description: "Archive relative to the workspace root"}
    member: {type: string, description: "read: exact member name as listed"}
    max: {type: integer, minimum: 1, maximum: 1000}
    max_chars: {type: integer, minimum: 100, maximum: 60000}
  required: [action, file]
output_schema:
  type: object
  properties:
    format: {type: string}
    verdict: {type: string, enum: [ok, caution, unsafe]}
    reasons: {type: array}
    listed: {type: array}
    problems: {type: array}
    text: {type: string}
layer:
  id: botboy.native.archive
  resources: {compute: medium, memory: 64MB, storage: workspace-read, network: false}
  security:
    sandbox: inprocess
    permissions: [fs.read.workspace]
    protection: pinned descriptor, never extracts, own max_length-bounded zlib/bz2/lzma decoding, 512MB decompression and 15s budgets, xz memlimit and zip LZMA dictionary cap, 4MB zip central-directory cap, 256KB tar header-read cap, 100k entry cap, escaped names
  runtime:
    type: builtin
    entry: botboy.skills.native.archive:run
    timeout: 30
---

# archive

    archive list downloads/project.zip
    archive check downloads/backup.tar.gz
    archive read downloads/project.zip src/README.md
    archive read logs/app.log.gz app.log

## Threat model

- The archive is untrusted. It is resolved through the workspace layer and opened
  through a descriptor pinned beneath the workspace root; nothing is extracted or
  written anywhere.
- Decompression bombs: all decompression is done by this skill with output-bounded
  `zlib`/`bz2`/`lzma` calls (at most 64 KiB per call; `zipfile`'s own bzip2/LZMA path
  is unbounded on older Pythons), charged to a 512 MB budget and a 15 s deadline.
  Declared sizes are never trusted: `check` decompresses every zip member and
  compares size and CRC with the central directory; a compressed tar is verified by
  its stream checksums. xz decoding has a 128 MB memory limit and zip LZMA members
  with dictionaries over 64 MB are refused.
- Parser resource use: zip central directories over 4 MB are refused before
  `zipfile` materialises them, tar header reads (GNU long names, pax records) are
  capped at 256 KB, at most 100 000 entries are scanned and member objects are
  dropped while iterating.
- Unsafe entries are reported, never acted on: absolute paths and drive letters,
  `..` traversal, symlinks/hardlinks and whether their targets escape the archive
  root, device files and FIFOs, setuid/setgid, duplicate and case-colliding names,
  Windows-reserved or invalid names, encrypted members, overlapping zip entries and
  local/central name mismatches (parser-confusion tricks), high compression ratios.
- Output hygiene: control, format, bidi and surrogate characters in names are
  escaped so a member name cannot forge or hide report lines.
- Remote use requires a server-issued approval (`remote_policy: approval`).

## Limitations

- zstd, 7z and rar are not supported; encrypted zip members are reported, not read.
- `read` on a tar scans to the end to find a later member of the same name (that is
  the one extraction keeps); if the budget runs out after a match, the result says so.
