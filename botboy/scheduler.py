from __future__ import annotations

import asyncio
import inspect
import json
import re
import secrets as _secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Callable, Dict, List, Optional

from botboy.db_mixin import _SQLiteMixin

_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    task_id     TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    schedule    TEXT NOT NULL,
    task_type   TEXT NOT NULL DEFAULT 'generic',
    payload     TEXT NOT NULL DEFAULT '{}',
    next_run_ts REAL NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1,
    max_runs    INTEGER,
    run_count   INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    principal   TEXT NOT NULL DEFAULT 'anonymous',
    org_id      TEXT NOT NULL DEFAULT 'default'
);
CREATE INDEX IF NOT EXISTS idx_st_next_run ON scheduled_tasks(next_run_ts, enabled);
"""

@dataclass
class ScheduledTask:
    task_id: str
    name: str
    schedule: str
    task_type: str
    payload: dict
    next_run_ts: float
    enabled: bool
    max_runs: Optional[int]
    run_count: int
    created_at: str
    principal: str
    org_id: str
    def to_dict(self) -> dict:
        return {"task_id": self.task_id, "name": self.name, "schedule": self.schedule,
                "task_type": self.task_type, "payload": self.payload, "next_run_ts": self.next_run_ts,
                "enabled": self.enabled, "max_runs": self.max_runs, "run_count": self.run_count,
                "created_at": self.created_at, "principal": self.principal, "org_id": self.org_id}

class SchedulerStore(_SQLiteMixin):
    """SQLite WAL persistence for scheduled tasks with principal/org ownership."""
    def __init__(self, db_path: str = ":memory:") -> None:
        self._init_connection_pool(db_path, _SCHEMA)
        self._ensure_security_schema()
    def _ensure_security_schema(self) -> None:
        conn = self._get_conn()
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(scheduled_tasks)").fetchall()}
        if "principal" not in columns: conn.execute("ALTER TABLE scheduled_tasks ADD COLUMN principal TEXT NOT NULL DEFAULT 'anonymous'")
        if "org_id" not in columns: conn.execute("ALTER TABLE scheduled_tasks ADD COLUMN org_id TEXT NOT NULL DEFAULT 'default'")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_st_owner ON scheduled_tasks(principal, org_id)")
        conn.commit()
    def add_task(self, name: str, schedule: str, task_type: str = "generic", payload: Optional[dict] = None,
                 max_runs: Optional[int] = None, principal: str = "anonymous", org_id: str = "default") -> str:
        try:
            from botboy.gateway.app_context import current_gateway_principal, current_gateway_org
            principal = current_gateway_principal() or principal
            org_id = current_gateway_org() or org_id
        except Exception: pass
        task_id = _secrets.token_hex(8); next_run = parse_schedule(schedule); ts = datetime.now(timezone.utc).isoformat()
        args = (task_id, name, schedule, task_type, json.dumps(payload or {}), next_run.timestamp(), max_runs, ts, principal or "anonymous", org_id or "default")
        sql = ("INSERT INTO scheduled_tasks (task_id,name,schedule,task_type,payload,next_run_ts,enabled,max_runs,run_count,created_at,principal,org_id) "
               "VALUES (?,?,?,?,?,?,1,?,0,?,?,?)")
        if self._is_memory:
            with self._shared_lock: self._shared_conn.execute(sql,args); self._shared_conn.commit()
        else:
            conn=self._get_conn(); conn.execute(sql,args); conn.commit()
        return task_id
    def get_due(self, now: Optional[float] = None) -> List[ScheduledTask]:
        ts=now if now is not None else time.time(); rows=self._fetchall("SELECT * FROM scheduled_tasks WHERE enabled=1 AND next_run_ts <= ? ORDER BY next_run_ts",(ts,)); return [self._row_to_task(r) for r in rows]
    def mark_done(self, task_id: str) -> None:
        conn=self._get_conn(); row=conn.execute("SELECT schedule,run_count,max_runs FROM scheduled_tasks WHERE task_id=?",(task_id,)).fetchone()
        if not row:return
        new_count=row["run_count"]+1; max_runs=row["max_runs"]
        if max_runs is not None and new_count>=max_runs: conn.execute("UPDATE scheduled_tasks SET enabled=0,run_count=? WHERE task_id=?",(new_count,task_id))
        else:
            try: next_run=CronExpression(row["schedule"]).next_run(); conn.execute("UPDATE scheduled_tasks SET next_run_ts=?,run_count=? WHERE task_id=?",(next_run.timestamp(),new_count,task_id))
            except ValueError: conn.execute("UPDATE scheduled_tasks SET enabled=0,run_count=? WHERE task_id=?",(new_count,task_id))
        conn.commit()
    def cancel(self, task_id: str, *, principal: str = "anonymous", org_id: str = "default", admin: bool = False) -> bool:
        try:
            from botboy.gateway.app_context import current_gateway_principal,current_gateway_org,current_gateway_roles
            principal=current_gateway_principal() or principal; org_id=current_gateway_org() or org_id; admin=admin or "admin" in {str(r).lower() for r in current_gateway_roles()}
        except Exception: pass
        sql="UPDATE scheduled_tasks SET enabled=0 WHERE task_id=? AND (?=1 OR (principal=? AND org_id=?))"; args=(task_id,int(admin),principal or "anonymous",org_id or "default")
        if self._is_memory:
            with self._shared_lock: self._shared_conn.execute(sql,args); self._shared_conn.commit()
        else: conn=self._get_conn(); conn.execute(sql,args); conn.commit()
        return True
    def list_tasks(self, enabled_only: bool = True, *, principal: Optional[str] = None, org_id: Optional[str] = None, admin: bool = False) -> List[ScheduledTask]:
        try:
            from botboy.gateway.app_context import current_gateway_principal,current_gateway_org,current_gateway_roles
            principal=principal or current_gateway_principal(); org_id=org_id or current_gateway_org(); admin=admin or "admin" in {str(r).lower() for r in current_gateway_roles()}
        except Exception: pass
        conditions=[]; params=[]
        if enabled_only: conditions.append("enabled=1")
        if not admin and principal: conditions.append("principal=?"); params.append(principal); conditions.append("org_id=?"); params.append(org_id or "default")
        q="SELECT * FROM scheduled_tasks"+(" WHERE "+" AND ".join(conditions) if conditions else "")+" ORDER BY next_run_ts"
        return [self._row_to_task(r) for r in self._fetchall(q,params)]
    def stats(self) -> dict:
        r=self._fetchone("SELECT COUNT(*) total,SUM(enabled) active,SUM(run_count) total_runs FROM scheduled_tasks"); return {"total":r["total"],"active":r["active"] or 0,"total_runs":r["total_runs"] or 0}
    def close(self): super().close()
    @staticmethod
    def _row_to_task(row):
        return ScheduledTask(task_id=row["task_id"],name=row["name"],schedule=row["schedule"],task_type=row["task_type"],payload=json.loads(row["payload"]),next_run_ts=row["next_run_ts"],enabled=bool(row["enabled"]),max_runs=row["max_runs"],run_count=row["run_count"],created_at=row["created_at"],principal=row["principal"] if "principal" in row.keys() else "anonymous",org_id=row["org_id"] if "org_id" in row.keys() else "default")

class BotBoyScheduler:
    _POLL_INTERVAL=10
    def __init__(self, db_path: str = ":memory:"):
        self._store=SchedulerStore(db_path); self._handlers={}; self._thread=None; self._stop_event=threading.Event(); self._running=False
    def register_handler(self, task_type: str, handler: Callable): self._handlers[task_type]=handler
    def add(self,name,schedule,payload=None,task_type="generic",max_runs=None,principal="anonymous",org_id="default"):
        return self._store.add_task(name,schedule,task_type=task_type,payload=payload,max_runs=max_runs,principal=principal,org_id=org_id)
    def cancel(self,task_id,*,principal="anonymous",org_id="default",admin=False): return self._store.cancel(task_id,principal=principal,org_id=org_id,admin=admin)
    def list_tasks(self,enabled_only=True,*,principal=None,org_id=None,admin=False): return self._store.list_tasks(enabled_only,principal=principal,org_id=org_id,admin=admin)
    def get_due_tasks(self): return self._store.get_due()
    def start(self):
        if self._running:return
        self._stop_event.clear(); self._running=True; self._thread=threading.Thread(target=self._run_loop,daemon=True,name="botboy-scheduler"); self._thread.start()
    def stop(self):
        self._stop_event.set(); self._running=False
        if self._thread:self._thread.join(timeout=15)
    def _run_loop(self):
        loop=asyncio.new_event_loop(); asyncio.set_event_loop(loop)
        try:
            while not self._stop_event.is_set():
                for task in self._store.get_due():
                    handler=self._handlers.get(task.task_type) or self._handlers.get("generic")
                    if handler:
                        try:
                            if inspect.iscoroutinefunction(handler): loop.run_until_complete(handler(task))
                            else: handler(task)
                        except Exception: pass
                    self._store.mark_done(task.task_id)
                self._stop_event.wait(timeout=self._POLL_INTERVAL)
        finally: loop.close()
    def stats(self):
        s=self._store.stats(); s["running"]=self._running; return s

# Original schedule parser support is intentionally kept below.
class CronExpression:
    def __init__(self, expr): self.expr=expr
    def next_run(self, now=None):
        from botboy.scheduler import parse_schedule as _parse
        raise ValueError("CronExpression fallback must be provided by the scheduler parser")

def parse_schedule(s: str):
    from datetime import datetime, timezone, timedelta
    s=s.strip().lower(); now=datetime.now(timezone.utc)
    if s.startswith("in "):
        m=re.match(r"in\\s+(\\d+)\\s*(second|seconds|minute|minutes|hour|hours|day|days)$",s)
        if not m: raise ValueError(f"Unknown relative schedule: {s!r}")
        n=int(m.group(1)); unit=m.group(2); return now+timedelta(**({"second":n,"seconds":n,"minute":n*1,"minutes":n,"hour":n,"hours":n,"day":n,"days":n} if unit in {"seconds","minutes","hours","days"} else {unit:n}))
    try:
        dt=datetime.fromisoformat(s)
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except ValueError: raise ValueError(f"Unsupported schedule: {s!r}")
