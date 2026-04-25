---
name: reminder
version: 2.0.0
description: >
  SQLite-backed persistent reminder and task management. Reminders survive across
  sessions in ~/.botboy/reminders.db. Use whenever the user wants to create a
  reminder, add a todo, track a task, or list pending items. Triggers on: remind,
  reminder, todo, task, checklist, or any request to track a future action.
triggers:
  - remind
  - reminder
  - todo
  - task
security_level: BEGINNER
layer:
  id: botboy.skills.reminder
  resources:
    compute: low
    memory: 4MB
    storage: ~/.botboy/reminders.db
    network: false
  security:
    sandbox: inprocess
    permissions: [storage.local]
  runtime:
    type: builtin
---

## Usage

    remind "Buy groceries"
    todo "Review PR #42"
    reminder list

## Implementation

```python
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
db = sqlite3.connect(str(Path.home()/".botboy"/"reminders.db"))
db.execute("CREATE TABLE IF NOT EXISTS reminders (id INTEGER PRIMARY KEY, text TEXT, done INTEGER DEFAULT 0, created_at TEXT)")
action = payload.get("action", "list")
if action == "add":
    db.execute("INSERT INTO reminders (text, created_at) VALUES (?, ?)",
               (payload.get("text",""), datetime.now(timezone.utc).isoformat()))
    db.commit()
    _result = {"added": payload.get("text"), "success": True}
else:
    rows = db.execute("SELECT id, text, created_at FROM reminders WHERE done=0 ORDER BY id").fetchall()
    _result = [{"id": r[0], "text": r[1], "created": r[2]} for r in rows]
db.close()
```
