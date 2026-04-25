---
name: json-tools
version: 2.0.0
description: >
  JSON formatting, validation, minification, and transformation. Use whenever the
  user wants to format, pretty-print, validate, minify, or query JSON data.
  Triggers on: json, format-json, validate-json, pretty-print, minify.
triggers:
  - json
  - format-json
  - validate-json
security_level: BEGINNER
layer:
  id: botboy.skills.json-tools
  resources:
    compute: low
    memory: 4MB
    network: false
  security:
    sandbox: inprocess
    permissions: []
  runtime:
    type: builtin
---

## Usage

    json format '{"name":"Alice","age":30}'
    json validate '{"key": "value"}'
    json minify '{ "a" : 1 , "b" : 2 }'

## Implementation

```python
import json
try:
    parsed = json.loads(payload.get("input", ""))
    _result = json.dumps(parsed, indent=2, ensure_ascii=False)
except json.JSONDecodeError as e:
    _result = {"error": str(e), "valid": False}
```
