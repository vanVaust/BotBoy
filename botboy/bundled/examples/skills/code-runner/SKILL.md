---
name: code-runner
version: 2.0.0
description: >
  Sandboxed Python code execution via subprocess isolation with resource limits.
  Use whenever the user wants to run, execute, or test Python code in an isolated
  environment. Triggers on: run, execute-code, code-run, or any request to execute
  Python code with safety guarantees.
triggers:
  - run
  - execute-code
  - code-run
security_level: INTERMEDIATE
layer:
  id: botboy.skills.code-runner
  resources:
    compute: high
    memory: 128MB
    network: false
  security:
    sandbox: subprocess
    permissions: []
  runtime:
    type: subprocess
    timeout: 10
---

## Usage

    run print("Hello BotBoy!")
    execute-code result = sum(range(100)); print(result)

## Implementation

```python
# Executed via SubprocessRuntime with resource limits:
# - Max RAM: 128MB, Timeout: 10 seconds, Network: none, Clean ENV
_result = {"executed": True}
```
