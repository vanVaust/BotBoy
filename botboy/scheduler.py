"""BotBoyScheduler — arithmetic cron parser + SQLite-WAL persistence."""
from __future__ import annotations

import asyncio
import inspect
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Dict, List, Optional

_WEEKDAY_NAMES = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}

class CronField:
    def __init__(self, expr: str, min_val: int, max_val: int) -> None:
        self.min_val=min_val; self.max_val=max_val; self._values=self._parse(expr.strip().lower())
    def _parse(self, expr: str) -> set:
        values=set()
        for part in expr.split(","):
            part=part.strip()
            for name,num in _WEEKDAY_NAMES.items(): part=part.replace(name,str(num))
            if part=="*": values.update(range(self.min_val,self.max_val+1))
            elif part.startswith("*/"): values.update(range(self.min_val,self.max_val+1,int(part[2:])))
            elif "-" in part:
                a,b=part.split("-",1)
                if "/" in b: b,step=b.split("/",1); values.update(range(int(a),int(b)+1,int(step)))
                else: values.update(range(int(a),int(b)+1))
            else: values.add(int(part))
        return values
    def matches(self,value:int)->bool:return value in self._values
    def next_after(self,current:int)->Optional[int]:
        for v in sorted(self._values):
            if v>=current:return v
        return None
    def first(self)->int:return min(self._values)

class CronExpression:
    def __init__(self,expr:str)->None:
        parts=expr.strip().split()
        if len(parts)!=5: raise ValueError(f"Invalid cron expression (need 5 fields): {expr!r}")
        self.minute=CronField(parts[0],0,59); self.hour=CronField(parts[1],0,23); self.day=CronField(parts[2],1,31); self.month=CronField(parts[3],1,12); self.weekday=CronField(parts[4],0,6); self._expr=expr
    def next_run(self,after:Optional[datetime]=None)->datetime:
        dt=(after or datetime.now(timezone.utc)).replace(second=0,microsecond=0)+timedelta(minutes=1)
        for _ in range(4*365*24*60):
            if not self.month.matches(dt.month):
                next_m=self.month.next_after(dt.month)
                if next_m is None: dt=dt.replace(year=dt.year+1,month=self.month.first(),day=1,hour=0,minute=0)
                else: dt=dt.replace(month=next_m,day=1,hour=0,minute=0)
                continue
            cron_weekday=(dt.weekday()+1)%7
            if not self.day.matches(dt.day) or not self.weekday.matches(cron_weekday): dt=(dt+timedelta(days=1)).replace(hour=0,minute=0); continue
            if not self.hour.matches(dt.hour):
                next_h=self.hour.next_after(dt.hour)
                if next_h is None: dt=(dt+timedelta(days=1)).replace(hour=self.hour.first(),minute=0)
                else: dt=dt.replace(hour=next_h,minute=0)
                continue
            next_m=self.minute.next_after(dt.minute)
            if next_m is None:
                next_h=self.hour.next_after(dt.hour+1)
                if next_h is None: dt=(dt+timedelta(days=1)).replace(hour=self.hour.first(),minute=0)
                else: dt=dt.replace(hour=next_h,minute=0)
                continue
            return dt.replace(minute=next_m)
        raise RuntimeError("CronExpression.next_run: could not find next run within 4 years")

def parse_schedule(schedule:str)->datetime:
    s=schedule.strip(); now=datetime.now(timezone.utc)
    if s.startswith("in "):
        rest=s[3:].strip()
        if rest.endswith("m"): return now+timedelta(minutes=int(rest[:-1]))
        if rest.endswith("h"): return now+timedelta(hours=int(rest[:-1]))
        if rest.endswith("d"): return now+timedelta(days=int(rest[:-1]))
        raise ValueError(f"Unknown relative schedule: {s!r}")
    try:
        dt=datetime.fromisoformat(s)
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except ValueError: pass
    return CronExpression(s).next_run(now)

_SCHEMA="""
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS scheduled_tasks (task_id TEXT PRIMARY KEY,name TEXT NOT NULL,schedule TEXT NOT NULL,task_type TEXT NOT NULL DEFAULT 'generic',payload TEXT NOT NULL DEFAULT '{}',next_run_ts REAL NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,max_runs INTEGER,run_count INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_st_next_run ON scheduled_tasks(next_run_ts, enabled);
"""
import json
import secrets as _secrets
from botboy.db_mixin import _SQLiteMixin

@dataclass
class ScheduledTask:
    task_id:str; name:str; schedule:str; task_type:str; payload:dict; next_run_ts:float; enabled:bool; max_runs:Optional[int]; run_count:int; created_at:str
    def to_dict(self)->dict:return {"task_id":self.task_id,"name":self.name,"schedule":self.schedule,"task_type":self.task_type,"payload":self.payload,"next_run_ts":self.next_run_ts,"enabled":self.enabled,"max_runs":self.max_runs,"run_count":self.run_count,"created_at":self.created_at}

class SchedulerStore(_SQLiteMixin):
    def __init__(self,db_path:str=":memory:")->None:
        self._init_connection_pool(db_path,_SCHEMA)
        self._ensure_security_columns()
    def _ensure_security_columns(self)->None:
        conn=self._shared_conn if self._is_memory else self._get_conn()
        rows=conn.execute("PRAGMA table_info(scheduled_tasks)").fetchall()
        columns={row[1] for row in rows}
        if "security_context" not in columns:
            conn.execute("ALTER TABLE scheduled_tasks ADD COLUMN security_context TEXT NOT NULL DEFAULT '{}'")
            conn.commit()
    def add_task(self,name:str,schedule:str,task_type:str="generic",payload:Optional[dict]=None,max_runs:Optional[int]=None,security_context:Optional[dict]=None)->str:
        task_id=_secrets.token_hex(8); next_run=parse_schedule(schedule); body=dict(payload or {})
        if security_context is not None:
            body["_botboy_security"]={"principal_id":str(security_context.get("principal_id", "")),"org_id":str(security_context.get("org_id", "")),"enforced":True,**{k:v for k,v in security_context.items() if k not in {"principal_id","org_id"}}}
        security=json.dumps(body.get("_botboy_security", {}),separators=(",",":"))
        args=(task_id,name,schedule,task_type,json.dumps(body),next_run.timestamp(),max_runs,datetime.now(timezone.utc).isoformat(),security)
        sql="INSERT INTO scheduled_tasks (task_id,name,schedule,task_type,payload,next_run_ts,enabled,max_runs,run_count,created_at,security_context) VALUES (?,?,?,?,?,?,1,?,0,?,?)"
        if self._is_memory:
            with self._shared_lock:self._shared_conn.execute(sql,args); self._shared_conn.commit()
        else: conn=self._get_conn(); conn.execute(sql,args); conn.commit()
        return task_id
    def get_due(self,now:Optional[float]=None)->List[ScheduledTask]:
        ts=now if now is not None else time.time(); return [self._row_to_task(r) for r in self._fetchall("SELECT * FROM scheduled_tasks WHERE enabled=1 AND next_run_ts <= ? ORDER BY next_run_ts",(ts,))]
    def mark_done(self,task_id:str)->None:
        conn=self._get_conn(); row=conn.execute("SELECT schedule,run_count,max_runs FROM scheduled_tasks WHERE task_id=?",(task_id,)).fetchone()
        if not row:return
        new_count=row["run_count"]+1; max_runs=row["max_runs"]
        if max_runs is not None and new_count>=max_runs: conn.execute("UPDATE scheduled_tasks SET enabled=0,run_count=? WHERE task_id=?",(new_count,task_id))
        else:
            try: conn.execute("UPDATE scheduled_tasks SET next_run_ts=?,run_count=? WHERE task_id=?",(CronExpression(row["schedule"]).next_run().timestamp(),new_count,task_id))
            except ValueError: conn.execute("UPDATE scheduled_tasks SET enabled=0,run_count=? WHERE task_id=?",(new_count,task_id))
        conn.commit()
    def cancel(self,task_id:str)->bool:
        if self._is_memory:
            with self._shared_lock:self._shared_conn.execute("UPDATE scheduled_tasks SET enabled=0 WHERE task_id=?",(task_id,)); self._shared_conn.commit()
        else: conn=self._get_conn(); conn.execute("UPDATE scheduled_tasks SET enabled=0 WHERE task_id=?",(task_id,)); conn.commit()
        return True
    def list_tasks(self,enabled_only:bool=True)->List[ScheduledTask]:
        q="SELECT * FROM scheduled_tasks"+(" WHERE enabled=1" if enabled_only else "")+" ORDER BY next_run_ts"; return [self._row_to_task(r) for r in self._fetchall(q)]
    def stats(self)->dict:
        r=self._fetchone("SELECT COUNT(*) total,SUM(enabled) active,SUM(run_count) total_runs FROM scheduled_tasks"); return {"total":r["total"],"active":r["active"] or 0,"total_runs":r["total_runs"] or 0}
    def close(self)->None:super().close()
    @staticmethod
    def _row_to_task(row)->ScheduledTask:return ScheduledTask(task_id=row["task_id"],name=row["name"],schedule=row["schedule"],task_type=row["task_type"],payload=json.loads(row["payload"]),next_run_ts=row["next_run_ts"],enabled=bool(row["enabled"]),max_runs=row["max_runs"],run_count=row["run_count"],created_at=row["created_at"])

class BotBoyScheduler:
    _POLL_INTERVAL=10
    def __init__(self,db_path:str=":memory:",security_validator:Optional[Callable[[ScheduledTask],bool]]=None)->None:
        self._store=SchedulerStore(db_path); self._handlers:Dict[str,Callable]={}; self._thread:Optional[threading.Thread]=None; self._stop_event=threading.Event(); self._running=False; self._security_validator=security_validator
    def register_handler(self,task_type:str,handler:Callable)->None:self._handlers[task_type]=handler
    def add(self,name:str,schedule:str,payload:Optional[dict]=None,task_type:str="generic",max_runs:Optional[int]=None,security_context:Optional[dict]=None)->str:return self._store.add_task(name,schedule,task_type=task_type,payload=payload,max_runs=max_runs,security_context=security_context)
    def cancel(self,task_id:str)->bool:return self._store.cancel(task_id)
    def list_tasks(self,enabled_only:bool=True)->List[ScheduledTask]:return self._store.list_tasks(enabled_only)
    def get_due_tasks(self)->List[ScheduledTask]:return self._store.get_due()
    def _security_allows(self,task:ScheduledTask)->bool:
        security=task.payload.get("_botboy_security") if isinstance(task.payload,dict) else None
        if not isinstance(security,dict) or not security.get("enforced"):
            return True
        if not str(security.get("principal_id", "")).strip() or not str(security.get("org_id", "")).strip():
            return False
        expires=security.get("approval_expires_at")
        if expires:
            try:
                expiry=datetime.fromisoformat(str(expires).replace("Z","+00:00"))
                if expiry <= datetime.now(timezone.utc):
                    return False
            except (TypeError,ValueError):
                return False
        if self._security_validator is not None:
            try:return bool(self._security_validator(task))
            except Exception:return False
        return True
    def start(self)->None:
        if self._running:return
        self._stop_event.clear(); self._running=True; self._thread=threading.Thread(target=self._run_loop,daemon=True,name="botboy-scheduler"); self._thread.start()
    def stop(self)->None:
        self._stop_event.set(); self._running=False
        if self._thread:self._thread.join(timeout=15)
    def _run_loop(self)->None:
        loop=asyncio.new_event_loop(); asyncio.set_event_loop(loop)
        try:
            while not self._stop_event.is_set():
                due=self._store.get_due()
                for task in due:
                    if not self._security_allows(task):
                        self._store.cancel(task.task_id)
                        continue
                    handler=self._handlers.get(task.task_type) or self._handlers.get("generic")
                    if handler:
                        try:
                            if inspect.iscoroutinefunction(handler):loop.run_until_complete(handler(task))
                            else:handler(task)
                        except Exception:pass
                    self._store.mark_done(task.task_id)
                self._stop_event.wait(timeout=self._POLL_INTERVAL)
        finally:loop.close()
    def stats(self)->dict:s=self._store.stats(); s["running"]=self._running; return s
