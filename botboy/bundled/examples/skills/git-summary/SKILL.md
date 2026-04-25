---
name: git-summary
version: 2.0.0
description: >
  Git repository summary including recent commits, authors, active branch, and
  file change statistics. Use whenever the user wants to summarise a git repo,
  see recent commits, check current branch, or get repository overview statistics.
  Triggers on: git-summary, git, repo-info.
triggers:
  - git-summary
  - git
  - repo-info
security_level: INTERMEDIATE
layer:
  id: botboy.skills.git-summary
  resources:
    compute: low
    memory: 16MB
    network: false
  security:
    sandbox: subprocess
    permissions: [filesystem.read]
  runtime:
    type: subprocess
---

## Usage

    git-summary /path/to/repo
    git /path/to/project

## Implementation

```python
import subprocess
path = payload.get("path", ".")
log = subprocess.run(["git","log","--oneline","-10"], capture_output=True, text=True, cwd=path, timeout=5)
branch = subprocess.run(["git","branch","--show-current"], capture_output=True, text=True, cwd=path, timeout=5)
_result = {"branch": branch.stdout.strip(), "recent_commits": log.stdout.strip().split("\n")}
```
