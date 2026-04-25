---
name: diff-compare
version: 2.0.0
description: >
  Unified and word-level diff comparison between two text blocks. Use whenever
  the user wants to compare texts, find differences, see what changed, or generate
  a diff. Triggers on: diff, compare, diff-compare, what-changed, or any request
  to show differences between two versions.
triggers:
  - diff
  - compare
  - diff-compare
security_level: BEGINNER
layer:
  id: botboy.skills.diff-compare
  resources:
    compute: low
    memory: 8MB
    network: false
  security:
    sandbox: inprocess
    permissions: []
  runtime:
    type: builtin
---

## Usage

    diff "old text version" "new text version"
    compare "line one\nline two" "line one\nline three"

## Implementation

```python
import difflib
a = payload.get("a","").splitlines(keepends=True)
b = payload.get("b","").splitlines(keepends=True)
diff = list(difflib.unified_diff(a, b, fromfile="original", tofile="modified", lineterm=""))
sm = difflib.SequenceMatcher(None, payload.get("a",""), payload.get("b",""))
_result = {"diff": "".join(diff) or "No differences.",
           "changed_lines": len([l for l in diff if l.startswith(("+","-")) and not l.startswith(("---","+++"))]),
           "similarity_ratio": round(sm.ratio(), 4)}
```
