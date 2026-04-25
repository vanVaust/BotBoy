---
name: regex-tools
version: 2.0.0
description: >
  Safe regular expression operations with ReDoS protection and timeout limits.
  Use for pattern matching, text extraction, and format validation. Triggers on
  regex, match, grep, pattern, extract-matches, validate-format, or any request
  to find patterns in text or validate format with a regular expression.
triggers:
  - regex
  - match
  - grep
  - pattern
security_level: BEGINNER
layer:
  id: botboy.skills.regex-tools
  resources:
    compute: low
    memory: 16MB
    network: false
  security:
    sandbox: inprocess
    permissions: []
    protection: timeout-dos-safe-redos-protected
  runtime:
    type: inprocess
    timeout: 2
---

## Usage

    regex match "hello world" "h\w+"
    regex find-all "user@example.com" "[\w.]+@[\w.]+"

## Implementation

```python
import re
# ReDoS protection: input capped at 64KB, pattern at 512 chars, timeout 2s
_result = re.compile(payload.get("pattern","")[:512]).findall(payload.get("text","")[:65536])
```
