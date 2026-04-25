"""Persistent task lifecycle store for BotBoy."""
from __future__ import annotations

import hashlib
import json
import secrets
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, List, Optional, Tuple

from botboy.db_mixin import _SQLiteMixin
from botboy.migrations import apply_task_store_migrations

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id            TEXT PRIMARY KEY,
    root_task_id       TEXT NOT NULL,
    parent_task_id     TEXT NOT NULL DEFAULT '',
    kind               TEXT NOT NULL DEFAULT 'command',
    owner              TEXT NOT NULL DEFAULT 'orchestrator',
    title              TEXT NOT NULL,
    summary            TEXT NOT NULL DEFAULT '',
    status             TEXT NOT NULL DEFAULT 'queued',
    principal          TEXT NOT NULL DEFAULT 'anonymous',
    request_id         TEXT NOT NULL DEFAULT '',
    run_id             TEXT NOT NULL DEFAULT '',
    scheduler_task_id  TEXT NOT NULL DEFAULT '',
    command            TEXT NOT NULL DEFAULT '',
    delegation_status  TEXT NOT NULL DEFAULT 'none',
    delegated_to_worker TEXT NOT NULL DEFAULT '',
    blocked_by_task_id TEXT NOT NULL DEFAULT '',
    blocked_kind       TEXT NOT NULL DEFAULT '',
    blocked_reason     TEXT NOT NULL DEFAULT '',
    lease_expires_at   TEXT NOT NULL DEFAULT '',
    heartbeat_at       TEXT NOT NULL DEFAULT '',
    attempt_count      INTEGER NOT NULL DEFAULT 0,
    retry_after_s      INTEGER NOT NULL DEFAULT 0,
    payload_json       TEXT NOT NULL DEFAULT '{}',
    result_json        TEXT NOT NULL DEFAULT '{}',
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    started_at         TEXT NOT NULL DEFAULT '',
    ended_at           TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_principal ON tasks(principal);
CREATE INDEX IF NOT EXISTS idx_tasks_request_id ON tasks(request_id);
CREATE INDEX IF NOT EXISTS idx_tasks_root_task_id ON tasks(root_task_id);
CREATE INDEX IF NOT EXISTS idx_tasks_scheduler_task_id ON tasks(scheduler_task_id);
CREATE INDEX IF NOT EXISTS idx_tasks_parent_task_id ON tasks(parent_task_id);

CREATE TABLE IF NOT EXISTS task_events (
    event_id      TEXT PRIMARY KEY,
    task_id       TEXT NOT NULL,
    event_type    TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT '',
    message       TEXT NOT NULL DEFAULT '',
    principal     TEXT NOT NULL DEFAULT 'anonymous',
    request_id    TEXT NOT NULL DEFAULT '',
    run_id        TEXT NOT NULL DEFAULT '',
    payload_ref   TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    FOREIGN KEY(task_id) REFERENCES tasks(task_id)
);
CREATE INDEX IF NOT EXISTS idx_task_events_task_id ON task_events(task_id);
CREATE INDEX IF NOT EXISTS idx_task_events_created ON task_events(created_at);

CREATE TABLE IF NOT EXISTS task_artifacts (
    artifact_id   TEXT PRIMARY KEY,
    task_id       TEXT NOT NULL,
    category      TEXT NOT NULL,
    label         TEXT NOT NULL,
    file_path     TEXT NOT NULL,
    media_type    TEXT NOT NULL DEFAULT 'application/octet-stream',
    size_bytes    INTEGER NOT NULL DEFAULT 0,
    sha256        TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    FOREIGN KEY(task_id) REFERENCES tasks(task_id)
);
CREATE INDEX IF NOT EXISTS idx_task_artifacts_task_id ON task_artifacts(task_id);
CREATE INDEX IF NOT EXISTS idx_task_artifacts_category ON task_artifacts(category);
"""

ACTIVE_TASK_STATUSES = {"queued", "running", "waiting_approval", "blocked"}
TASK_STATUS_QUEUED = "queued"
TASK_STATUS_RUNNING = "running"
TASK_STATUS_WAITING_APPROVAL = "waiting_approval"
TASK_STATUS_BLOCKED = "blocked"
TASK_STATUS_COMPLETED = "completed"
TASK_STATUS_FAILED = "failed"
TASK_STATUS_CANCELLED = "cancelled"
DELEGATION_STATUS_NONE = "none"
DELEGATION_STATUS_DELEGATED = "delegated"
DELEGATION_STATUS_LEASED = "leased"
DELEGATION_STATUS_BLOCKED_ON_CHILD = "blocked_on_child"
DELEGATION_STATUS_AWAITING_MERGE = "awaiting_merge"
LEASE_TTL_SECONDS = 900

FIXED_WORKER_PROFILES = {
    "planner": {
        "worker_id": "planner",
        "display_name": "Planner",
        "role": "planner",
        "capabilities": ["decomposition", "routing", "task planning"],
        "max_concurrency": 1,
        "approval_profile": "strict",
    },
    "researcher": {
        "worker_id": "researcher",
        "display_name": "Researcher",
        "role": "researcher",
        "capabilities": ["source discovery", "fact finding", "context gathering"],
        "max_concurrency": 1,
        "approval_profile": "normal",
    },
    "executor": {
        "worker_id": "executor",
        "display_name": "Executor",
        "role": "executor",
        "capabilities": ["implementation", "patching", "tool execution"],
        "max_concurrency": 1,
        "approval_profile": "guarded",
    },
    "reviewer": {
        "worker_id": "reviewer",
        "display_name": "Reviewer",
        "role": "reviewer",
        "capabilities": ["verification", "risk analysis", "regression review"],
        "max_concurrency": 1,
        "approval_profile": "strict",
    },
    "designer": {
        "worker_id": "designer",
        "display_name": "Designer",
        "role": "designer",
        "capabilities": ["ux framing", "workflow design", "visual structure"],
        "max_concurrency": 1,
        "approval_profile": "normal",
    },
}


@dataclass(frozen=True)
class TaskContext:
    task_id: str
    root_task_id: str
    parent_task_id: str = ""
    kind: str = "command"
    owner: str = "orchestrator"
    principal: str = "anonymous"
    request_id: str = ""
    scheduler_task_id: str = ""
    command: str = ""
    status: str = TASK_STATUS_QUEUED
    run_id: str = ""
    delegation_status: str = DELEGATION_STATUS_NONE
    delegated_to_worker: str = ""
    blocked_by_task_id: str = ""
    blocked_kind: str = ""
    blocked_reason: str = ""
    lease_expires_at: str = ""
    heartbeat_at: str = ""
    attempt_count: int = 0
    retry_after_s: int = 0


@dataclass
class TaskRecord:
    task_id: str
    root_task_id: str
    parent_task_id: str
    kind: str
    owner: str
    title: str
    summary: str
    status: str
    principal: str
    request_id: str
    run_id: str
    scheduler_task_id: str
    command: str
    delegation_status: str
    delegated_to_worker: str
    blocked_by_task_id: str
    blocked_kind: str
    blocked_reason: str
    lease_expires_at: str
    heartbeat_at: str
    attempt_count: int
    retry_after_s: int
    payload_json: str
    result_json: str
    created_at: str
    updated_at: str
    started_at: str
    ended_at: str

    @property
    def payload(self) -> dict:
        return _safe_json_loads(self.payload_json)

    @property
    def result(self) -> dict:
        return _safe_json_loads(self.result_json)

    def to_context(self) -> TaskContext:
        return TaskContext(
            task_id=self.task_id,
            root_task_id=self.root_task_id,
            parent_task_id=self.parent_task_id,
            kind=self.kind,
            owner=self.owner,
            principal=self.principal,
            request_id=self.request_id,
            scheduler_task_id=self.scheduler_task_id,
            command=self.command,
            status=self.status,
            run_id=self.run_id,
            delegation_status=self.delegation_status,
            delegated_to_worker=self.delegated_to_worker,
            blocked_by_task_id=self.blocked_by_task_id,
            blocked_kind=self.blocked_kind,
            blocked_reason=self.blocked_reason,
            lease_expires_at=self.lease_expires_at,
            heartbeat_at=self.heartbeat_at,
            attempt_count=self.attempt_count,
            retry_after_s=self.retry_after_s,
        )

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "root_task_id": self.root_task_id,
            "parent_task_id": self.parent_task_id,
            "kind": self.kind,
            "owner": self.owner,
            "title": self.title,
            "summary": self.summary,
            "status": self.status,
            "principal": self.principal,
            "request_id": self.request_id,
            "run_id": self.run_id,
            "scheduler_task_id": self.scheduler_task_id,
            "command": self.command,
            "delegation_status": self.delegation_status,
            "delegated_to_worker": self.delegated_to_worker,
            "blocked_by_task_id": self.blocked_by_task_id,
            "blocked_kind": self.blocked_kind,
            "blocked_reason": self.blocked_reason,
            "lease_expires_at": self.lease_expires_at,
            "heartbeat_at": self.heartbeat_at,
            "attempt_count": self.attempt_count,
            "retry_after_s": self.retry_after_s,
            "payload": self.payload,
            "result": self.result,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
        }


@dataclass
class TaskEventRecord:
    event_id: str
    task_id: str
    event_type: str
    status: str
    message: str
    principal: str
    request_id: str
    run_id: str
    payload_ref: str
    created_at: str

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "task_id": self.task_id,
            "event_type": self.event_type,
            "status": self.status,
            "message": self.message,
            "principal": self.principal,
            "request_id": self.request_id,
            "run_id": self.run_id,
            "payload_ref": self.payload_ref,
            "created_at": self.created_at,
        }


@dataclass
class TaskArtifactRecord:
    artifact_id: str
    task_id: str
    category: str
    label: str
    file_path: str
    media_type: str
    size_bytes: int
    sha256: str
    created_at: str

    def to_dict(self) -> dict:
        return {
            "artifact_id": self.artifact_id,
            "task_id": self.task_id,
            "category": self.category,
            "label": self.label,
            "file_path": self.file_path,
            "media_type": self.media_type,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "created_at": self.created_at,
        }


@dataclass
class WorkerNodeRecord:
    node_id: str
    worker_id: str
    queue_name: str
    display_name: str
    endpoint: str
    node_status: str
    drain_state: str
    last_seen_ip: str
    metadata_json: str
    capacity_json: str
    registered_at: str
    last_heartbeat_at: str
    heartbeat_expires_at: str
    updated_at: str

    @property
    def metadata(self) -> dict:
        return _safe_json_loads(self.metadata_json)

    @property
    def capacity(self) -> dict:
        return _safe_json_loads(self.capacity_json)

    def to_dict(
        self,
        *,
        capabilities: Optional[list[str]] = None,
        queue: Optional[dict] = None,
        reference_time: Optional[datetime] = None,
    ) -> dict:
        now = reference_time or datetime.now(timezone.utc)
        expiry = _safe_parse_datetime(self.heartbeat_expires_at)
        is_stale = bool(expiry and expiry < now)
        is_draining = self.drain_state in {"draining", "drained"} or self.node_status == "draining"
        if self.drain_state == "drained":
            effective_status = "drained"
        elif is_draining:
            effective_status = "draining"
        elif is_stale:
            effective_status = "stale"
        else:
            effective_status = self.node_status or "ready"
        health = str(self.metadata.get("health", "unknown") or "unknown")
        load_value = self.metadata.get("load")
        return {
            "node_id": self.node_id,
            "worker_id": self.worker_id,
            "queue_name": self.queue_name,
            "display_name": self.display_name,
            "endpoint": self.endpoint,
            "node_status": self.node_status,
            "drain_state": self.drain_state,
            "effective_status": effective_status,
            "last_seen_ip": self.last_seen_ip,
            "metadata": self.metadata,
            "capacity": self.capacity,
            "registered_at": self.registered_at,
            "last_heartbeat_at": self.last_heartbeat_at,
            "heartbeat_expires_at": self.heartbeat_expires_at,
            "updated_at": self.updated_at,
            "is_stale": is_stale,
            "is_healthy": not is_stale and effective_status in {"ready", "running", "draining"},
            "draining": effective_status == "draining",
            "health": health,
            "load": load_value,
            "capabilities": list(capabilities or []),
            "queue": queue,
        }


@dataclass
class ExecutionQueueRecord:
    queue_name: str
    worker_id: str
    queue_status: str
    lease_ttl_seconds: int
    max_parallelism: int
    metadata_json: str
    created_at: str
    updated_at: str

    @property
    def metadata(self) -> dict:
        return _safe_json_loads(self.metadata_json)

    def to_dict(self) -> dict:
        return {
            "queue_name": self.queue_name,
            "worker_id": self.worker_id,
            "queue_status": self.queue_status,
            "lease_ttl_seconds": int(self.lease_ttl_seconds or 0),
            "max_parallelism": int(self.max_parallelism or 0),
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class QueueLeaseRecord:
    lease_id: str
    queue_name: str
    task_id: str
    node_id: str
    lease_status: str
    lease_expires_at: str
    metadata_json: str
    created_at: str
    updated_at: str

    @property
    def metadata(self) -> dict:
        return _safe_json_loads(self.metadata_json)

    def to_dict(self, *, reference_time: Optional[datetime] = None) -> dict:
        now = reference_time or datetime.now(timezone.utc)
        expires_at = _safe_parse_datetime(self.lease_expires_at)
        is_expired = bool(self.lease_status == "active" and expires_at and expires_at <= now)
        effective_status = "expired" if is_expired else self.lease_status
        return {
            "lease_id": self.lease_id,
            "queue_name": self.queue_name,
            "task_id": self.task_id,
            "node_id": self.node_id,
            "lease_status": effective_status,
            "stored_lease_status": self.lease_status,
            "lease_expires_at": self.lease_expires_at,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "is_expired": is_expired,
            "is_active": effective_status == "active",
        }


def _safe_json_loads(payload: str) -> dict:
    try:
        value = json.loads(payload or "{}")
        return value if isinstance(value, dict) else {"value": value}
    except json.JSONDecodeError:
        return {}


def _safe_parse_datetime(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class TaskStore(_SQLiteMixin):
    """SQLite-backed task lifecycle store with filesystem artifact references."""

    def __init__(self, db_path: str = ":memory:", artifact_root: str = "") -> None:
        self._init_connection_pool(db_path, _SCHEMA)
        self._ensure_schema()
        root = artifact_root.strip() if artifact_root else ""
        if not root:
            if db_path == ":memory:":
                root = str((Path.cwd() / ".botboy_artifacts" / "tasks").resolve())
            else:
                root = str((Path(db_path).expanduser().resolve().parent / "artifacts" / "tasks"))
        self.artifact_root = Path(root).expanduser()
        self.artifact_root.mkdir(parents=True, exist_ok=True)

    def _ensure_schema(self) -> None:
        conn = self._get_conn()
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(tasks)").fetchall()
        }
        migrations = {
            "delegation_status": "ALTER TABLE tasks ADD COLUMN delegation_status TEXT NOT NULL DEFAULT 'none'",
            "delegated_to_worker": "ALTER TABLE tasks ADD COLUMN delegated_to_worker TEXT NOT NULL DEFAULT ''",
            "blocked_by_task_id": "ALTER TABLE tasks ADD COLUMN blocked_by_task_id TEXT NOT NULL DEFAULT ''",
            "blocked_kind": "ALTER TABLE tasks ADD COLUMN blocked_kind TEXT NOT NULL DEFAULT ''",
            "blocked_reason": "ALTER TABLE tasks ADD COLUMN blocked_reason TEXT NOT NULL DEFAULT ''",
            "lease_expires_at": "ALTER TABLE tasks ADD COLUMN lease_expires_at TEXT NOT NULL DEFAULT ''",
            "heartbeat_at": "ALTER TABLE tasks ADD COLUMN heartbeat_at TEXT NOT NULL DEFAULT ''",
            "attempt_count": "ALTER TABLE tasks ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0",
            "retry_after_s": "ALTER TABLE tasks ADD COLUMN retry_after_s INTEGER NOT NULL DEFAULT 0",
        }
        changed = False
        for name, sql in migrations.items():
            if name not in columns:
                conn.execute(sql)
                changed = True
        if changed:
            conn.commit()
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_parent_task_id ON tasks(parent_task_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_delegation_status ON tasks(delegation_status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_delegated_to_worker ON tasks(delegated_to_worker)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_blocked_by_task_id ON tasks(blocked_by_task_id)")
        conn.commit()
        apply_task_store_migrations(conn)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _expires_at(*, seconds: int = LEASE_TTL_SECONDS) -> str:
        return (datetime.now(timezone.utc) + timedelta(seconds=max(1, int(seconds or LEASE_TTL_SECONDS)))).isoformat()

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}-{secrets.token_hex(8)}"

    @staticmethod
    def _json(value: Optional[dict]) -> str:
        return json.dumps(value or {}, sort_keys=True)

    @staticmethod
    def _json_hash(value: Optional[dict]) -> str:
        payload = json.dumps(value or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _path_is_within(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def _artifact_root_path(self) -> Path:
        return self.artifact_root.resolve()

    def _artifact_target_path(self, task_id: str, filename: str) -> Path:
        normalized_filename = str(filename or "").strip()
        if not normalized_filename:
            raise ValueError("Missing artifact filename")
        task_dir = self.task_dir(task_id).resolve()
        target = (task_dir / normalized_filename).resolve()
        if not self._path_is_within(target, task_dir):
            raise ValueError("Artifact path escapes task artifact directory.")
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def _artifact_storage_path(
        self,
        task_id: str,
        *,
        category: str,
        source_path: Path,
        artifact_id: str,
        allow_external_source: bool = False,
    ) -> Path:
        source = source_path.expanduser().resolve()
        artifact_root = self._artifact_root_path()
        if self._path_is_within(source, artifact_root):
            return source
        if not allow_external_source:
            raise ValueError("Artifact source must stay within artifact_root.")
        safe_category = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(category or "").strip())
        suffix = "".join(source.suffixes) or source.suffix
        target = self._artifact_target_path(
            task_id,
            f"{safe_category or 'artifact'}_{artifact_id}{suffix}",
        )
        shutil.copyfile(source, target)
        return target

    def task_dir(self, task_id: str) -> Path:
        path = self.artifact_root / task_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def create_task(
        self,
        *,
        title: str,
        kind: str = "command",
        owner: str = "orchestrator",
        principal: str = "anonymous",
        request_id: str = "",
        command: str = "",
        payload: Optional[dict] = None,
        parent_task_id: str = "",
        root_task_id: str = "",
        scheduler_task_id: str = "",
        status: str = "queued",
        summary: str = "",
        run_id: str = "",
        delegation_status: str = DELEGATION_STATUS_NONE,
        delegated_to_worker: str = "",
        blocked_by_task_id: str = "",
        blocked_kind: str = "",
        blocked_reason: str = "",
        lease_expires_at: str = "",
        heartbeat_at: str = "",
        attempt_count: int = 0,
        retry_after_s: int = 0,
        result: Optional[dict] = None,
    ) -> TaskContext:
        task_id = self._new_id("task")
        root_id = root_task_id or task_id
        ts = self._now()
        attempt_total = int(attempt_count or 0)
        if attempt_total <= 0 and status == TASK_STATUS_RUNNING:
            attempt_total = 1
        values = (
            task_id,
            root_id,
            parent_task_id or "",
            kind,
            owner,
            title,
            summary,
            status,
            principal or "anonymous",
            request_id or "",
            run_id or "",
            scheduler_task_id or "",
            command or "",
            delegation_status or DELEGATION_STATUS_NONE,
            delegated_to_worker or "",
            blocked_by_task_id or "",
            blocked_kind or "",
            blocked_reason or "",
            lease_expires_at or "",
            heartbeat_at or "",
            attempt_total,
            int(retry_after_s or 0),
            self._json(payload),
            self._json(result),
            ts,
            ts,
            ts if status == "running" else "",
            ts if status in {"completed", "failed", "cancelled"} else "",
        )
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO tasks (task_id, root_task_id, parent_task_id, kind, owner, title, summary, status,"
            " principal, request_id, run_id, scheduler_task_id, command, delegation_status,"
            " delegated_to_worker, blocked_by_task_id, blocked_kind, blocked_reason, lease_expires_at,"
            " heartbeat_at, attempt_count, retry_after_s, payload_json, result_json,"
            " created_at, updated_at, started_at, ended_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            values,
        )
        conn.commit()
        self.add_event(
            task_id,
            event_type="created",
            status=status,
            message=summary or title,
            principal=principal,
            request_id=request_id,
            run_id=run_id,
        )
        record = self.get_task(task_id)
        return record.to_context()

    def start_task(
        self,
        task_id: str,
        *,
        principal: str = "anonymous",
        request_id: str = "",
        run_id: str = "",
        message: str = "Task started",
    ) -> Optional[TaskRecord]:
        ts = self._now()
        record = self.get_task(task_id)
        attempt_count = (record.attempt_count if record else 0) + 1
        delegation_status = (
            DELEGATION_STATUS_LEASED if record and record.delegated_to_worker else None
        )
        heartbeat_at = ts if record and record.delegated_to_worker else None
        lease_expires_at = ""
        if heartbeat_at:
            try:
                lease_expires_at = (
                    datetime.fromisoformat(heartbeat_at.replace("Z", "+00:00"))
                    + timedelta(seconds=LEASE_TTL_SECONDS)
                ).isoformat()
            except ValueError:
                lease_expires_at = ""
        conn = self._get_conn()
        conn.execute(
            "UPDATE tasks SET status = 'running', principal = ?, request_id = ?, run_id = ?, updated_at = ?,"
            " started_at = CASE WHEN started_at = '' THEN ? ELSE started_at END,"
            " attempt_count = ?, delegation_status = COALESCE(?, delegation_status),"
            " heartbeat_at = COALESCE(?, heartbeat_at), lease_expires_at = COALESCE(?, lease_expires_at) WHERE task_id = ?",
            (
                principal or "anonymous",
                request_id or "",
                run_id or "",
                ts,
                ts,
                attempt_count,
                delegation_status,
                heartbeat_at,
                lease_expires_at or None,
                task_id,
            ),
        )
        conn.commit()
        self.add_event(
            task_id,
            event_type="started",
            status="running",
            message=message,
            principal=principal,
            request_id=request_id,
            run_id=run_id,
        )
        record = self.get_task(task_id)
        return record

    def update_task(
        self,
        task_id: str,
        *,
        owner: Optional[str] = None,
        status: Optional[str] = None,
        summary: Optional[str] = None,
        principal: Optional[str] = None,
        request_id: Optional[str] = None,
        run_id: Optional[str] = None,
        delegation_status: Optional[str] = None,
        delegated_to_worker: Optional[str] = None,
        blocked_by_task_id: Optional[str] = None,
        blocked_kind: Optional[str] = None,
        blocked_reason: Optional[str] = None,
        lease_expires_at: Optional[str] = None,
        heartbeat_at: Optional[str] = None,
        attempt_count: Optional[int] = None,
        retry_after_s: Optional[int] = None,
        payload: Optional[dict] = None,
        result: Optional[dict] = None,
        ended: bool = False,
    ) -> Optional[TaskRecord]:
        assignments = ["updated_at = ?"]
        params: list[Any] = [self._now()]
        if owner is not None:
            assignments.append("owner = ?")
            params.append(owner or "orchestrator")
        if status is not None:
            assignments.append("status = ?")
            params.append(status)
        if summary is not None:
            assignments.append("summary = ?")
            params.append(summary)
        if principal is not None:
            assignments.append("principal = ?")
            params.append(principal or "anonymous")
        if request_id is not None:
            assignments.append("request_id = ?")
            params.append(request_id or "")
        if run_id is not None:
            assignments.append("run_id = ?")
            params.append(run_id or "")
        if delegation_status is not None:
            assignments.append("delegation_status = ?")
            params.append(delegation_status or DELEGATION_STATUS_NONE)
        if delegated_to_worker is not None:
            assignments.append("delegated_to_worker = ?")
            params.append(delegated_to_worker or "")
        if blocked_by_task_id is not None:
            assignments.append("blocked_by_task_id = ?")
            params.append(blocked_by_task_id or "")
        if blocked_kind is not None:
            assignments.append("blocked_kind = ?")
            params.append(blocked_kind or "")
        if blocked_reason is not None:
            assignments.append("blocked_reason = ?")
            params.append(blocked_reason or "")
        if lease_expires_at is not None:
            assignments.append("lease_expires_at = ?")
            params.append(lease_expires_at or "")
        if heartbeat_at is not None:
            assignments.append("heartbeat_at = ?")
            params.append(heartbeat_at or "")
        if attempt_count is not None:
            assignments.append("attempt_count = ?")
            params.append(int(attempt_count))
        if retry_after_s is not None:
            assignments.append("retry_after_s = ?")
            params.append(int(retry_after_s))
        if payload is not None:
            assignments.append("payload_json = ?")
            params.append(self._json(payload))
        if result is not None:
            assignments.append("result_json = ?")
            params.append(self._json(result))
        if ended:
            assignments.append("ended_at = ?")
            params.append(self._now())
        params.append(task_id)
        conn = self._get_conn()
        conn.execute(f"UPDATE tasks SET {', '.join(assignments)} WHERE task_id = ?", params)
        conn.commit()
        return self.get_task(task_id)

    def attach_run(self, task_id: str, run_id: str) -> Optional[TaskRecord]:
        return self.update_task(task_id, run_id=run_id)

    def mark_running(
        self,
        task_id: str,
        *,
        principal: str = "anonymous",
        request_id: str = "",
        run_id: str = "",
        message: str = "Task started",
    ) -> Optional[TaskRecord]:
        return self.start_task(
            task_id,
            principal=principal,
            request_id=request_id,
            run_id=run_id,
            message=message,
        )

    def finish_task(
        self,
        task_id: str,
        *,
        status: str,
        summary: str = "",
        principal: str = "anonymous",
        request_id: str = "",
        run_id: str = "",
        result: Optional[dict] = None,
        event_type: str = "completed",
        event_message: str = "",
    ) -> Optional[TaskRecord]:
        record = self.update_task(
            task_id,
            status=status,
            summary=summary,
            principal=principal,
            request_id=request_id,
            run_id=run_id,
            result=result,
            ended=True,
        )
        self.add_event(
            task_id,
            event_type=event_type,
            status=status,
            message=event_message or summary or status,
            principal=principal,
            request_id=request_id,
            run_id=run_id,
        )
        return record

    def update_status(
        self,
        task_id: str,
        *,
        status: str,
        summary: str = "",
        principal: str = "anonymous",
        request_id: str = "",
        run_id: str = "",
        result: Optional[dict] = None,
        event_type: str = "updated",
        message: str = "",
        payload_ref: str = "",
    ) -> Optional[TaskRecord]:
        ended = status in {TASK_STATUS_COMPLETED, TASK_STATUS_FAILED, TASK_STATUS_CANCELLED}
        record = self.update_task(
            task_id,
            status=status,
            summary=summary,
            principal=principal,
            request_id=request_id,
            run_id=run_id,
            result=result,
            ended=ended,
        )
        self.add_event(
            task_id,
            event_type=event_type,
            status=status,
            message=message or summary or status,
            principal=principal,
            request_id=request_id,
            run_id=run_id,
            payload_ref=payload_ref,
        )
        return record

    def cancel_task(
        self,
        task_id: str,
        *,
        principal: str = "anonymous",
        request_id: str = "",
        reason: str = "cancelled",
    ) -> Optional[TaskRecord]:
        return self.finish_task(
            task_id,
            status="cancelled",
            summary=reason,
            principal=principal,
            request_id=request_id,
            event_type="cancelled",
            event_message=reason,
        )

    def create_child_task(
        self,
        parent_task_id: str,
        *,
        worker_id: str,
        title: str,
        kind: str = "worker",
        owner: str = "",
        principal: str = "anonymous",
        request_id: str = "",
        command: str = "",
        payload: Optional[dict] = None,
        summary: str = "",
        status: str = TASK_STATUS_QUEUED,
        delegation_status: str = DELEGATION_STATUS_DELEGATED,
        blocked_reason: str = "",
        block_parent: bool = True,
    ) -> TaskContext:
        parent = self.get_task(parent_task_id)
        if not parent:
            raise ValueError(f"Unknown parent task: {parent_task_id}")
        child = self.create_task(
            title=title,
            kind=kind,
            owner=owner or f"worker:{worker_id}",
            principal=principal,
            request_id=request_id,
            command=command,
            payload=payload,
            parent_task_id=parent.task_id,
            root_task_id=parent.root_task_id or parent.task_id,
            status=status,
            summary=summary,
            delegation_status=delegation_status,
            delegated_to_worker=worker_id,
        )
        self.add_event(
            parent.task_id,
            event_type="handoff_spawned",
            status=parent.status,
            message=summary or f"Delegated to worker:{worker_id}",
            principal=principal,
            request_id=request_id,
            payload_ref=child.task_id,
        )
        if block_parent:
            self.update_task(
                parent.task_id,
                blocked_by_task_id=child.task_id,
                blocked_kind="child",
                blocked_reason=blocked_reason or title,
                delegation_status=DELEGATION_STATUS_BLOCKED_ON_CHILD,
            )
        return child

    def get_children(self, task_id: str, *, limit: int = 100) -> List[TaskRecord]:
        return self.list_children(task_id, limit=limit)

    def list_children(self, task_id: str, *, limit: int = 100) -> List[TaskRecord]:
        rows = self._fetchall(
            "SELECT * FROM tasks WHERE parent_task_id = ? ORDER BY created_at ASC LIMIT ?",
            (task_id, limit),
        )
        return [self._row_to_task(row) for row in rows]

    def get_blockers(self, task_id: str, *, limit: int = 100) -> List[TaskRecord]:
        record = self.get_task(task_id)
        if not record or not record.blocked_by_task_id:
            return []
        blocker = self.get_task(record.blocked_by_task_id)
        return [blocker] if blocker else []

    def get_blocked_tasks(self, task_id: str, *, limit: int = 100) -> List[TaskRecord]:
        rows = self._fetchall(
            "SELECT * FROM tasks WHERE blocked_by_task_id = ? ORDER BY updated_at DESC LIMIT ?",
            (task_id, limit),
        )
        return [self._row_to_task(row) for row in rows]

    def list_blockers(self, *, limit: int = 100) -> List[TaskRecord]:
        rows = self._fetchall(
            "SELECT * FROM tasks WHERE status IN (?, ?) OR blocked_by_task_id != '' ORDER BY updated_at DESC LIMIT ?",
            (TASK_STATUS_WAITING_APPROVAL, TASK_STATUS_BLOCKED, limit),
        )
        return [self._row_to_task(row) for row in rows]

    def task_graph(self, task_id: str) -> dict:
        record = self.get_task(task_id)
        if not record:
            return {"task": None, "children": [], "root_task_id": ""}
        root_task_id = record.root_task_id or record.task_id
        records, _ = self.list_tasks(limit=500, root_task_id=root_task_id)
        root = next((item for item in records if item.task_id == root_task_id), record)
        children = self.list_children(root_task_id, limit=500)
        return {
            "root_task_id": root_task_id,
            "task": record.to_dict(),
            "root": root.to_dict(),
            "children": [item.to_dict() for item in children],
            "tasks": [item.to_dict() for item in records],
        }

    @staticmethod
    def _parse_iso_ts(value: str) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    def _lease_snapshot(self, record: TaskRecord, *, lease_timeout_s: int = LEASE_TTL_SECONDS) -> dict:
        anchor_raw = record.heartbeat_at or record.updated_at or record.created_at
        anchor = self._parse_iso_ts(anchor_raw)
        now = datetime.now(timezone.utc)
        age_s = max(0, int((now - anchor).total_seconds())) if anchor else 0
        derived_expires_at = (
            (anchor + timedelta(seconds=lease_timeout_s)).isoformat()
            if anchor
            else record.lease_expires_at
        )
        is_stale = bool(
            record.delegated_to_worker
            and record.status == TASK_STATUS_RUNNING
            and anchor
            and age_s > int(lease_timeout_s)
        )
        return {
            "task_id": record.task_id,
            "root_task_id": record.root_task_id,
            "parent_task_id": record.parent_task_id,
            "worker_id": record.delegated_to_worker,
            "owner": record.owner,
            "status": record.status,
            "delegation_status": record.delegation_status,
            "title": record.title,
            "summary": record.summary,
            "request_id": record.request_id,
            "heartbeat_at": anchor_raw,
            "lease_expires_at": derived_expires_at,
            "lease_age_s": age_s,
            "lease_timeout_s": int(lease_timeout_s),
            "is_stale": is_stale,
            "is_recoverable": bool(record.parent_task_id and is_stale),
        }

    def list_worker_leases(
        self,
        *,
        limit: int = 100,
        only_stale: bool = False,
        lease_timeout_s: int = LEASE_TTL_SECONDS,
    ) -> List[dict]:
        rows = self._fetchall(
            "SELECT * FROM tasks WHERE delegated_to_worker != '' AND status IN (?, ?, ?, ?) ORDER BY updated_at DESC LIMIT ?",
            (
                TASK_STATUS_QUEUED,
                TASK_STATUS_RUNNING,
                TASK_STATUS_WAITING_APPROVAL,
                TASK_STATUS_BLOCKED,
                limit,
            ),
        )
        leases = [self._lease_snapshot(self._row_to_task(row), lease_timeout_s=lease_timeout_s) for row in rows]
        if only_stale:
            leases = [item for item in leases if item["is_stale"]]
        return leases

    def recover_stale_worker_task(
        self,
        task_id: str,
        *,
        principal: str = "anonymous",
        request_id: str = "",
        run_id: str = "",
        lease_timeout_s: int = LEASE_TTL_SECONDS,
    ) -> Optional[dict]:
        record = self.get_task(task_id)
        if not record:
            return None
        lease = self._lease_snapshot(record, lease_timeout_s=lease_timeout_s)
        if not lease["is_recoverable"]:
            return None
        updated = self.update_task(
            task_id,
            status=TASK_STATUS_QUEUED,
            delegation_status=DELEGATION_STATUS_DELEGATED,
            blocked_by_task_id="",
            blocked_kind="",
            blocked_reason="",
            lease_expires_at="",
            heartbeat_at="",
            retry_after_s=0,
        )
        self.add_event(
            task_id,
            event_type="lease_recovered",
            status=TASK_STATUS_QUEUED,
            message=f"Recovered stale worker lease for {record.delegated_to_worker}",
            principal=principal,
            request_id=request_id,
            run_id=run_id,
            payload_ref=record.delegated_to_worker,
        )
        parent = self.get_task(record.parent_task_id) if record.parent_task_id else None
        if parent:
            parent = self.update_task(
                parent.task_id,
                status=TASK_STATUS_BLOCKED,
                delegation_status=DELEGATION_STATUS_BLOCKED_ON_CHILD,
                blocked_by_task_id=task_id,
                blocked_kind="lease_recovery",
                blocked_reason=f"Recovered stale worker:{record.delegated_to_worker} lease; child requeued",
                retry_after_s=lease_timeout_s,
            )
            self.add_event(
                parent.task_id,
                event_type="child_recovered",
                status=TASK_STATUS_BLOCKED,
                message=f"Recovered stale lease on child {task_id}",
                principal=principal,
                request_id=request_id,
                run_id=run_id,
                payload_ref=task_id,
            )
        return {
            "task": updated.to_dict() if updated else record.to_dict(),
            "parent_task": parent.to_dict() if parent else None,
            "lease": lease,
        }

    def reassign_task(
        self,
        task_id: str,
        *,
        worker_id: str,
        principal: str = "anonymous",
        request_id: str = "",
        run_id: str = "",
    ) -> Optional[TaskRecord]:
        record = self.update_task(
            task_id,
            owner=f"worker:{worker_id}",
            delegated_to_worker=worker_id,
            delegation_status=DELEGATION_STATUS_DELEGATED,
        )
        self.add_event(
            task_id,
            event_type="reassigned",
            status=record.status if record else "",
            message=f"Task reassigned to worker:{worker_id}",
            principal=principal,
            request_id=request_id,
            run_id=run_id,
            payload_ref=worker_id,
        )
        return record

    def list_workers(self) -> List[dict]:
        return self.worker_summary()["workers"]

    def worker_summary(self) -> dict:
        rows = self._fetchall(
            "SELECT delegated_to_worker, COUNT(*) AS cnt FROM tasks WHERE delegated_to_worker != '' GROUP BY delegated_to_worker"
        )
        active_rows = self._fetchall(
            "SELECT delegated_to_worker, COUNT(*) AS cnt FROM tasks WHERE delegated_to_worker != '' AND status IN (?, ?) GROUP BY delegated_to_worker",
            (TASK_STATUS_QUEUED, TASK_STATUS_RUNNING),
        )
        by_worker = {row["delegated_to_worker"]: row["cnt"] for row in rows}
        active_by_worker = {row["delegated_to_worker"]: row["cnt"] for row in active_rows}
        workers = [
            {
                **profile,
                "task_total": by_worker.get(worker_id, 0),
                "active_count": active_by_worker.get(worker_id, 0),
            }
            for worker_id, profile in FIXED_WORKER_PROFILES.items()
        ]
        return {
            "by_worker": by_worker,
            "active_by_worker": active_by_worker,
            "worker_count": len(FIXED_WORKER_PROFILES),
            "workers": workers,
        }

    def register_worker_node(
        self,
        *,
        node_id: str,
        worker_id: str,
        display_name: str = "",
        endpoint: str = "",
        capabilities: Optional[list[str]] = None,
        queue_name: str = "",
        lease_ttl_seconds: int = LEASE_TTL_SECONDS,
        max_parallelism: int = 1,
        max_concurrency: int = 0,
        metadata: Optional[dict] = None,
        last_seen_ip: str = "",
        node_status: str = "ready",
    ) -> dict:
        normalized_node_id = str(node_id or "").strip()
        normalized_worker_id = str(worker_id or "").strip().lower()
        if not normalized_node_id:
            raise ValueError("Missing node_id")
        if normalized_worker_id not in FIXED_WORKER_PROFILES:
            raise ValueError(f"Unknown worker_id '{worker_id}'.")
        profile = FIXED_WORKER_PROFILES[normalized_worker_id]
        normalized_queue_name = str(queue_name or "").strip() or f"{normalized_worker_id}.{normalized_node_id}"
        ttl_seconds = max(30, int(lease_ttl_seconds or LEASE_TTL_SECONDS))
        parallelism = max(1, int(max_parallelism or max_concurrency or 1))
        existing_record = self._get_worker_node_record(normalized_node_id)
        existing_queue = self.get_execution_queue(normalized_queue_name)
        preserved_drain_state = (
            str(existing_record.drain_state or "").strip()
            if existing_record and str(existing_record.drain_state or "").strip() in {"draining", "drained"}
            else "active"
        )
        preserved_queue_status = (
            str((existing_queue or {}).get("queue_status", "") or "").strip()
            if existing_queue and str((existing_queue or {}).get("queue_status", "") or "").strip() not in {"", "ready"}
            else "ready"
        )
        queue_record = self.upsert_execution_queue(
            queue_name=normalized_queue_name,
            worker_id=normalized_worker_id,
            lease_ttl_seconds=ttl_seconds,
            max_parallelism=parallelism,
            queue_status=preserved_queue_status,
            metadata={"source": "worker_node_register"},
        )
        now = self._now()
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO worker_nodes (
                node_id,
                worker_id,
                queue_name,
                display_name,
                endpoint,
                node_status,
                drain_state,
                last_seen_ip,
                metadata_json,
                capacity_json,
                registered_at,
                last_heartbeat_at,
                heartbeat_expires_at,
                updated_at
            )
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(node_id) DO UPDATE SET
                worker_id = excluded.worker_id,
                queue_name = excluded.queue_name,
                display_name = excluded.display_name,
                endpoint = excluded.endpoint,
                node_status = excluded.node_status,
                drain_state = excluded.drain_state,
                last_seen_ip = excluded.last_seen_ip,
                metadata_json = excluded.metadata_json,
                capacity_json = excluded.capacity_json,
                last_heartbeat_at = excluded.last_heartbeat_at,
                heartbeat_expires_at = excluded.heartbeat_expires_at,
                updated_at = excluded.updated_at
            """,
            (
                normalized_node_id,
                normalized_worker_id,
                normalized_queue_name,
                str(display_name or "").strip() or f"{profile['display_name']} Node {normalized_node_id}",
                str(endpoint or "").strip(),
                "draining" if preserved_drain_state != "active" else (str(node_status or "ready").strip() or "ready"),
                preserved_drain_state,
                str(last_seen_ip or "").strip(),
                self._json(metadata),
                self._json(
                    {
                        "max_parallelism": parallelism,
                        "lease_ttl_seconds": ttl_seconds,
                    }
                ),
                now,
                now,
                self._expires_at(seconds=ttl_seconds),
                now,
            ),
        )
        conn.execute("DELETE FROM worker_capabilities WHERE node_id = ?", (normalized_node_id,))
        for capability in capabilities or list(profile["capabilities"]):
            normalized_capability = str(capability or "").strip()
            if not normalized_capability:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO worker_capabilities (node_id, capability, created_at) VALUES (?,?,?)",
                (normalized_node_id, normalized_capability, now),
            )
        conn.commit()
        node = self.get_worker_node(normalized_node_id)
        if not node:
            raise RuntimeError(f"Failed to register worker node '{normalized_node_id}'.")
        node["queue"] = queue_record
        return node

    def heartbeat_worker_node(
        self,
        node_id: str,
        *,
        node_status: str = "ready",
        metadata: Optional[dict] = None,
        last_seen_ip: str = "",
        health: str = "",
        load: Optional[float] = None,
    ) -> Optional[dict]:
        record = self._get_worker_node_record(node_id)
        if not record:
            return None
        ttl_seconds = max(30, int(record.capacity.get("lease_ttl_seconds", LEASE_TTL_SECONDS) or LEASE_TTL_SECONDS))
        now = self._now()
        merged_metadata = record.metadata
        if isinstance(metadata, dict):
            merged_metadata.update(metadata)
        if health:
            merged_metadata["health"] = str(health).strip()
        if load is not None:
            merged_metadata["load"] = float(load)
        effective_node_status = (
            "draining"
            if str(record.drain_state or "").strip() in {"draining", "drained"}
            else (str(node_status or record.node_status).strip() or record.node_status or "ready")
        )
        effective_queue_status = "draining" if effective_node_status == "draining" else "ready"
        conn = self._get_conn()
        conn.execute(
            """
            UPDATE worker_nodes
            SET node_status = ?, last_seen_ip = ?, metadata_json = ?, last_heartbeat_at = ?, heartbeat_expires_at = ?, updated_at = ?
            WHERE node_id = ?
            """,
            (
                effective_node_status,
                str(last_seen_ip or "").strip() or record.last_seen_ip,
                self._json(merged_metadata),
                now,
                self._expires_at(seconds=ttl_seconds),
                now,
                record.node_id,
            ),
        )
        conn.execute(
            "UPDATE execution_queues SET queue_status = ?, updated_at = ? WHERE queue_name = ?",
            (effective_queue_status, now, record.queue_name),
        )
        conn.commit()
        return self.get_worker_node(record.node_id)

    def drain_worker_node(
        self,
        node_id: str,
        *,
        reason: str = "",
        metadata: Optional[dict] = None,
    ) -> Optional[dict]:
        record = self._get_worker_node_record(node_id)
        if not record:
            return None
        merged_metadata = record.metadata
        if reason:
            merged_metadata["drain_reason"] = reason
        if isinstance(metadata, dict):
            merged_metadata.update(metadata)
        now = self._now()
        conn = self._get_conn()
        conn.execute(
            """
            UPDATE worker_nodes
            SET node_status = ?, drain_state = ?, metadata_json = ?, updated_at = ?
            WHERE node_id = ?
            """,
            ("draining", "draining", self._json(merged_metadata), now, record.node_id),
        )
        conn.execute(
            "UPDATE execution_queues SET queue_status = ?, updated_at = ? WHERE queue_name = ?",
            ("draining", now, record.queue_name),
        )
        conn.commit()
        return self.get_worker_node(record.node_id)

    def get_worker_node(self, node_id: str) -> Optional[dict]:
        record = self._get_worker_node_record(node_id)
        if not record:
            return None
        capabilities = self._worker_capabilities([record.node_id]).get(record.node_id, [])
        queue = self.get_execution_queue(record.queue_name)
        return record.to_dict(capabilities=capabilities, queue=queue)

    def list_worker_nodes(self, *, worker_id: str = "", include_stale: bool = True) -> list[dict]:
        conditions: list[str] = []
        params: list[Any] = []
        normalized_worker_id = str(worker_id or "").strip().lower()
        if normalized_worker_id:
            conditions.append("worker_id = ?")
            params.append(normalized_worker_id)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self._fetchall(
            f"SELECT * FROM worker_nodes {where} ORDER BY worker_id ASC, node_id ASC",
            tuple(params),
        )
        records = [self._row_to_worker_node(row) for row in rows]
        capabilities_map = self._worker_capabilities([record.node_id for record in records])
        queue_lookup = self._queue_lookup([record.queue_name for record in records])
        items: list[dict] = []
        for record in records:
            payload = record.to_dict(
                capabilities=capabilities_map.get(record.node_id, []),
                queue=queue_lookup.get(record.queue_name),
            )
            if include_stale or not payload["is_stale"]:
                items.append(payload)
        return items

    def worker_node_summary(self) -> dict:
        nodes = self.list_worker_nodes(include_stale=True)
        queues = self.list_execution_queues()
        lease_summary = self.queue_summary()
        by_worker: dict[str, int] = {}
        by_effective_status: dict[str, int] = {}
        healthy_count = 0
        stale_count = 0
        draining_count = 0
        for node in nodes:
            worker_id = str(node.get("worker_id", "") or "")
            by_worker[worker_id] = by_worker.get(worker_id, 0) + 1
            status = str(node.get("effective_status", "") or "unknown")
            by_effective_status[status] = by_effective_status.get(status, 0) + 1
            if node.get("is_healthy"):
                healthy_count += 1
            if node.get("is_stale"):
                stale_count += 1
            if node.get("effective_status") == "draining":
                draining_count += 1
        return {
            "available": True,
            "node_count": len(nodes),
            "healthy_count": healthy_count,
            "stale_count": stale_count,
            "draining_count": draining_count,
            "queue_count": len(queues),
            "lease_count": lease_summary["lease_count"],
            "active_lease_count": lease_summary["active_lease_count"],
            "expired_lease_count": lease_summary["expired_lease_count"],
            "released_lease_count": lease_summary["released_lease_count"],
            "by_worker": dict(sorted(by_worker.items())),
            "by_effective_status": dict(sorted(by_effective_status.items())),
        }

    def upsert_execution_queue(
        self,
        *,
        queue_name: str,
        worker_id: str,
        lease_ttl_seconds: int = LEASE_TTL_SECONDS,
        max_parallelism: int = 1,
        queue_status: str = "ready",
        metadata: Optional[dict] = None,
    ) -> dict:
        normalized_queue_name = str(queue_name or "").strip()
        normalized_worker_id = str(worker_id or "").strip().lower()
        if not normalized_queue_name:
            raise ValueError("Missing queue_name")
        if not normalized_worker_id:
            raise ValueError("Missing worker_id")
        now = self._now()
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO execution_queues (
                queue_name,
                worker_id,
                queue_status,
                lease_ttl_seconds,
                max_parallelism,
                metadata_json,
                created_at,
                updated_at
            )
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(queue_name) DO UPDATE SET
                worker_id = excluded.worker_id,
                queue_status = excluded.queue_status,
                lease_ttl_seconds = excluded.lease_ttl_seconds,
                max_parallelism = excluded.max_parallelism,
                metadata_json = excluded.metadata_json,
                updated_at = excluded.updated_at
            """,
            (
                normalized_queue_name,
                normalized_worker_id,
                str(queue_status or "ready").strip() or "ready",
                max(30, int(lease_ttl_seconds or LEASE_TTL_SECONDS)),
                max(1, int(max_parallelism or 1)),
                self._json(metadata),
                now,
                now,
            ),
        )
        conn.commit()
        queue = self.get_execution_queue(normalized_queue_name)
        if not queue:
            raise RuntimeError(f"Failed to upsert execution queue '{normalized_queue_name}'.")
        return queue

    def get_execution_queue(self, queue_name: str) -> Optional[dict]:
        row = self._fetchone("SELECT * FROM execution_queues WHERE queue_name = ?", (str(queue_name or "").strip(),))
        return self._row_to_execution_queue(row).to_dict() if row else None

    def list_execution_queues(self, *, worker_id: str = "") -> list[dict]:
        conditions: list[str] = []
        params: list[Any] = []
        normalized_worker_id = str(worker_id or "").strip().lower()
        if normalized_worker_id:
            conditions.append("worker_id = ?")
            params.append(normalized_worker_id)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self._fetchall(
            f"SELECT * FROM execution_queues {where} ORDER BY worker_id ASC, queue_name ASC",
            tuple(params),
        )
        return [self._row_to_execution_queue(row).to_dict() for row in rows]

    @staticmethod
    def _retry_due(record: TaskRecord, *, reference_time: Optional[datetime] = None) -> bool:
        retry_after_s = int(record.retry_after_s or 0)
        if retry_after_s <= 0:
            return True
        anchor = _safe_parse_datetime(record.updated_at or record.created_at)
        if not anchor:
            return True
        now = reference_time or datetime.now(timezone.utc)
        return anchor + timedelta(seconds=retry_after_s) <= now

    def _expire_queue_leases(self, *, reference_time: Optional[str] = None) -> int:
        now = reference_time or self._now()
        conn = self._get_conn()
        cursor = conn.execute(
            """
            UPDATE queue_leases
            SET lease_status = 'expired', updated_at = ?
            WHERE lease_status = 'active'
              AND lease_expires_at != ''
              AND lease_expires_at <= ?
            """,
            (now, now),
        )
        conn.commit()
        return int(cursor.rowcount or 0)

    def claim_next_queue_lease(
        self,
        *,
        queue_name: str,
        node_id: str,
        worker_id: str = "",
        task_id: str = "",
        principal: str = "anonymous",
        request_id: str = "",
        run_id: str = "",
        lease_ttl_seconds: int = 0,
        limit: int = 50,
        metadata: Optional[dict] = None,
    ) -> Optional[dict]:
        normalized_queue_name = str(queue_name or "").strip()
        normalized_node_id = str(node_id or "").strip()
        normalized_worker_id = str(worker_id or "").strip().lower()
        normalized_task_id = str(task_id or "").strip()
        if not normalized_queue_name:
            raise ValueError("Missing queue_name")
        if not normalized_node_id:
            raise ValueError("Missing node_id")

        queue = self.get_execution_queue(normalized_queue_name)
        if not queue:
            raise ValueError(f"Unknown queue_name '{queue_name}'.")
        node = self.get_worker_node(normalized_node_id)
        if not node:
            raise ValueError(f"Unknown worker node '{node_id}'.")
        node_worker_id = str(node.get("worker_id") or "").strip().lower()
        if normalized_worker_id and normalized_worker_id != node_worker_id:
            raise ValueError(f"Node '{node_id}' is registered for worker '{node_worker_id}'.")
        normalized_worker_id = normalized_worker_id or node_worker_id
        if node["queue_name"] != normalized_queue_name:
            raise ValueError(f"Node '{node_id}' is not assigned to queue '{queue_name}'.")
        if str(queue.get("queue_status", "")).lower() != "ready":
            raise ValueError(f"Queue '{queue_name}' is not ready.")
        if node.get("draining") or node.get("effective_status") in {"draining", "drained", "stale"}:
            raise ValueError(f"Node '{node_id}' cannot claim tasks while {node.get('effective_status')}.")

        now = self._now()
        now_dt = _safe_parse_datetime(now) or datetime.now(timezone.utc)
        self._expire_queue_leases(reference_time=now)
        ttl_seconds = max(30, int(lease_ttl_seconds or queue.get("lease_ttl_seconds") or LEASE_TTL_SECONDS))
        expires_at = self._expires_at(seconds=ttl_seconds)
        candidate_limit = max(1, min(int(limit or 50), 500))
        owner = f"worker:{normalized_worker_id}"
        conn = self._get_conn()
        lease_id = self._new_id("lease")
        chosen: Optional[TaskRecord] = None
        began = False
        try:
            conn.execute("BEGIN IMMEDIATE")
            began = True
            active_count = int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM queue_leases
                    WHERE queue_name = ? AND lease_status = 'active'
                      AND (lease_expires_at = '' OR lease_expires_at > ?)
                    """,
                    (normalized_queue_name, now),
                ).fetchone()[0]
            )
            max_parallelism = max(1, int(queue.get("max_parallelism") or 1))
            if active_count >= max_parallelism:
                conn.commit()
                return None
            task_filter = "AND task_id = ?" if normalized_task_id else ""
            params: list[Any] = [TASK_STATUS_QUEUED, normalized_worker_id, owner]
            if normalized_task_id:
                params.append(normalized_task_id)
            params.append(candidate_limit)
            rows = conn.execute(
                f"""
                SELECT * FROM tasks
                WHERE status = ?
                  AND (delegated_to_worker = ? OR owner = ?)
                  {task_filter}
                ORDER BY updated_at ASC, created_at ASC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
            for row in rows:
                record = self._row_to_task(row)
                if self._retry_due(record, reference_time=now_dt):
                    chosen = record
                    break
            if not chosen:
                conn.commit()
                return None
            fencing_token = self._new_id("fence")
            payload = {
                "principal": principal,
                "request_id": request_id,
                "run_id": run_id,
                "claimed_at": now,
                "lease_ttl_seconds": ttl_seconds,
                "claim_contract": "claim_next_queue_lease",
                "fencing_token": fencing_token,
            }
            if isinstance(metadata, dict):
                payload.update(metadata)
            conn.execute(
                """
                INSERT INTO queue_leases (
                    lease_id, queue_name, task_id, node_id, lease_status,
                    lease_expires_at, metadata_json, created_at, updated_at
                )
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    lease_id,
                    normalized_queue_name,
                    chosen.task_id,
                    normalized_node_id,
                    "active",
                    expires_at,
                    self._json(payload),
                    now,
                    now,
                ),
            )
            cursor = conn.execute(
                """
                UPDATE tasks
                SET status = ?,
                    owner = ?,
                    delegated_to_worker = ?,
                    delegation_status = ?,
                    lease_expires_at = ?,
                    heartbeat_at = ?,
                    retry_after_s = 0,
                    updated_at = ?
                WHERE task_id = ? AND status = ?
                """,
                (
                    TASK_STATUS_RUNNING,
                    owner,
                    normalized_worker_id,
                    DELEGATION_STATUS_LEASED,
                    expires_at,
                    now,
                    now,
                    chosen.task_id,
                    TASK_STATUS_QUEUED,
                ),
            )
            if cursor.rowcount <= 0:
                conn.rollback()
                return None
            conn.commit()
        except Exception:
            if began:
                conn.rollback()
            raise
        self.add_event(
            chosen.task_id,
            event_type="queue_lease_claimed",
            status=TASK_STATUS_RUNNING,
            message=f"Queue lease {lease_id} claimed by node {normalized_node_id}",
            principal=principal,
            request_id=request_id,
            run_id=run_id,
            payload_ref=lease_id,
        )
        task = self.get_task(chosen.task_id)
        return {
            "lease": self.get_queue_lease(lease_id, include_inactive=True),
            "task": task.to_dict() if task else chosen.to_dict(),
            "summary": self.queue_summary(),
        }

    def acquire_queue_lease(
        self,
        *,
        queue_name: str,
        node_id: str,
        task_id: str = "",
        principal: str = "anonymous",
        request_id: str = "",
        run_id: str = "",
        lease_ttl_seconds: int = 0,
        metadata: Optional[dict] = None,
    ) -> dict:
        normalized_queue_name = str(queue_name or "").strip()
        normalized_node_id = str(node_id or "").strip()
        normalized_task_id = str(task_id or "").strip()
        if not normalized_queue_name:
            raise ValueError("Missing queue_name")
        if not normalized_node_id:
            raise ValueError("Missing node_id")

        queue = self.get_execution_queue(normalized_queue_name)
        if not queue:
            raise ValueError(f"Unknown queue_name '{queue_name}'.")
        node = self.get_worker_node(normalized_node_id)
        if not node:
            raise ValueError(f"Unknown worker node '{node_id}'.")
        if node["queue_name"] != normalized_queue_name:
            raise ValueError(f"Node '{node_id}' is not assigned to queue '{queue_name}'.")
        if str(queue.get("queue_status", "")).lower() != "ready":
            raise ValueError(f"Queue '{queue_name}' is not ready.")
        if node.get("draining") or node.get("effective_status") in {"draining", "drained", "stale"}:
            raise ValueError(f"Node '{node_id}' cannot acquire new leases while {node.get('effective_status')}.")

        now = self._now()
        ttl_seconds = max(30, int(lease_ttl_seconds or queue.get("lease_ttl_seconds") or LEASE_TTL_SECONDS))
        expires_at = self._expires_at(seconds=ttl_seconds)
        conn = self._get_conn()
        began = False
        try:
            conn.execute("BEGIN IMMEDIATE")
            began = True
            conn.execute(
                """
                UPDATE queue_leases
                SET lease_status = 'expired', updated_at = ?
                WHERE lease_status = 'active'
                  AND lease_expires_at != ''
                  AND lease_expires_at <= ?
                """,
                (now, now),
            )
            existing = conn.execute(
                """
                SELECT * FROM queue_leases
                WHERE queue_name = ? AND node_id = ? AND task_id = ? AND lease_status = 'active'
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (normalized_queue_name, normalized_node_id, normalized_task_id),
            ).fetchone()
            if existing:
                conn.commit()
                return self.renew_queue_lease(
                    existing["lease_id"],
                    principal=principal,
                    request_id=request_id,
                    run_id=run_id,
                    lease_ttl_seconds=ttl_seconds,
                    metadata=metadata,
                    fencing_token=str(_safe_json_loads(existing["metadata_json"]).get("fencing_token", "") or ""),
                )

            active_count = int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM queue_leases
                    WHERE queue_name = ? AND lease_status = 'active' AND (lease_expires_at = '' OR lease_expires_at > ?)
                    """,
                    (normalized_queue_name, now),
                ).fetchone()[0]
            )
            max_parallelism = max(1, int(queue.get("max_parallelism") or 1))
            if active_count >= max_parallelism:
                conn.rollback()
                raise ValueError(f"Queue '{queue_name}' is at max_parallelism ({max_parallelism}).")

            lease_id = self._new_id("lease")
            fencing_token = self._new_id("fence")
            payload = {
                "principal": principal,
                "request_id": request_id,
                "run_id": run_id,
                "acquired_at": now,
                "lease_ttl_seconds": ttl_seconds,
                "fencing_token": fencing_token,
            }
            if isinstance(metadata, dict):
                payload.update(metadata)
            conn.execute(
                """
                INSERT INTO queue_leases (
                    lease_id, queue_name, task_id, node_id, lease_status,
                    lease_expires_at, metadata_json, created_at, updated_at
                )
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    lease_id,
                    normalized_queue_name,
                    normalized_task_id,
                    normalized_node_id,
                    "active",
                    expires_at,
                    self._json(payload),
                    now,
                    now,
                ),
            )
            task = self.get_task(normalized_task_id) if normalized_task_id else None
            if task:
                if task.status not in {TASK_STATUS_QUEUED, TASK_STATUS_RUNNING}:
                    conn.rollback()
                    raise ValueError(
                        f"Task '{normalized_task_id}' is not leaseable from status '{task.status}'."
                    )
                worker_id = str(node.get("worker_id") or "")
                cursor = conn.execute(
                    """
                    UPDATE tasks
                    SET status = ?,
                        owner = ?,
                        delegated_to_worker = ?,
                        delegation_status = ?,
                        lease_expires_at = ?,
                        heartbeat_at = ?,
                        updated_at = ?
                    WHERE task_id = ? AND status IN (?, ?)
                    """,
                    (
                        TASK_STATUS_RUNNING,
                        f"worker:{worker_id}",
                        worker_id,
                        DELEGATION_STATUS_LEASED,
                        expires_at,
                        now,
                        now,
                        normalized_task_id,
                        TASK_STATUS_QUEUED,
                        TASK_STATUS_RUNNING,
                    ),
                )
                if cursor.rowcount <= 0:
                    conn.rollback()
                    raise ValueError(
                        f"Task '{normalized_task_id}' could not transition to an active lease."
                    )
            conn.commit()
        except Exception:
            if began:
                conn.rollback()
            raise
        if task:
            self.add_event(
                normalized_task_id,
                event_type="queue_lease_acquired",
                status=TASK_STATUS_RUNNING,
                message=f"Queue lease {lease_id} acquired by node {normalized_node_id}",
                principal=principal,
                request_id=request_id,
                run_id=run_id,
                payload_ref=lease_id,
            )
        return self.get_queue_lease(lease_id)  # type: ignore[return-value]

    def renew_queue_lease(
        self,
        lease_id: str,
        *,
        principal: str = "anonymous",
        request_id: str = "",
        run_id: str = "",
        lease_ttl_seconds: int = 0,
        metadata: Optional[dict] = None,
        fencing_token: str = "",
    ) -> dict:
        normalized_lease_id = str(lease_id or "").strip()
        if not normalized_lease_id:
            raise ValueError("Missing lease_id")
        self._expire_queue_leases()
        lease = self.get_queue_lease(normalized_lease_id, include_inactive=True)
        if not lease:
            raise ValueError(f"Unknown queue lease '{lease_id}'.")
        if lease["lease_status"] != "active":
            raise ValueError(f"Queue lease '{lease_id}' is {lease['lease_status']}.")
        normalized_fencing_token = str(fencing_token or "").strip()
        if normalized_fencing_token:
            lease_fencing_token = str((lease.get("metadata") or {}).get("fencing_token", "") or "")
            if lease_fencing_token and normalized_fencing_token != lease_fencing_token:
                raise ValueError(f"Queue lease '{lease_id}' rejected a stale fencing token.")
        queue = self.get_execution_queue(lease["queue_name"])
        ttl_seconds = max(30, int(lease_ttl_seconds or (queue or {}).get("lease_ttl_seconds") or LEASE_TTL_SECONDS))
        now = self._now()
        expires_at = self._expires_at(seconds=ttl_seconds)
        payload = dict(lease.get("metadata") or {})
        payload.update(
            {
                "principal": principal,
                "request_id": request_id,
                "run_id": run_id,
                "renewed_at": now,
                "lease_ttl_seconds": ttl_seconds,
            }
        )
        if isinstance(metadata, dict):
            payload.update(metadata)
        conn = self._get_conn()
        conn.execute(
            """
            UPDATE queue_leases
            SET lease_expires_at = ?, metadata_json = ?, updated_at = ?
            WHERE lease_id = ? AND lease_status = 'active'
            """,
            (expires_at, self._json(payload), now, normalized_lease_id),
        )
        if lease["task_id"] and self.get_task(lease["task_id"]):
            conn.execute(
                "UPDATE tasks SET lease_expires_at = ?, heartbeat_at = ?, updated_at = ? WHERE task_id = ?",
                (expires_at, now, now, lease["task_id"]),
            )
        conn.commit()
        if lease["task_id"] and self.get_task(lease["task_id"]):
            self.add_event(
                lease["task_id"],
                event_type="queue_lease_renewed",
                status=TASK_STATUS_RUNNING,
                message=f"Queue lease {normalized_lease_id} renewed",
                principal=principal,
                request_id=request_id,
                run_id=run_id,
                payload_ref=normalized_lease_id,
            )
        return self.get_queue_lease(normalized_lease_id, include_inactive=True)  # type: ignore[return-value]

    def release_queue_lease(
        self,
        lease_id: str,
        *,
        principal: str = "anonymous",
        request_id: str = "",
        run_id: str = "",
        reason: str = "",
        metadata: Optional[dict] = None,
        node_id: str = "",
        worker_id: str = "",
        fencing_token: str = "",
    ) -> dict:
        normalized_lease_id = str(lease_id or "").strip()
        if not normalized_lease_id:
            raise ValueError("Missing lease_id")
        lease = self.get_queue_lease(normalized_lease_id, include_inactive=True)
        if not lease:
            raise ValueError(f"Unknown queue lease '{lease_id}'.")
        normalized_node_id = str(node_id or "").strip()
        normalized_worker_id = str(worker_id or "").strip().lower()
        normalized_fencing_token = str(fencing_token or "").strip()
        if normalized_node_id and normalized_node_id != str(lease.get("node_id", "")):
            raise ValueError(f"Queue lease '{lease_id}' is not assigned to node '{normalized_node_id}'.")
        lease_node = self.get_worker_node(str(lease.get("node_id", "") or ""))
        if normalized_worker_id and lease_node and normalized_worker_id != str(lease_node.get("worker_id", "")).lower():
            raise ValueError(f"Queue lease '{lease_id}' is not assigned to worker '{normalized_worker_id}'.")
        lease_fencing_token = str((lease.get("metadata") or {}).get("fencing_token", "") or "")
        if normalized_fencing_token and lease_fencing_token and normalized_fencing_token != lease_fencing_token:
            raise ValueError(f"Queue lease '{lease_id}' rejected a stale fencing token.")
        now = self._now()
        payload = dict(lease.get("metadata") or {})
        payload.update(
            {
                "principal": principal,
                "request_id": request_id,
                "run_id": run_id,
                "released_at": now,
                "release_reason": reason,
            }
        )
        if isinstance(metadata, dict):
            payload.update(metadata)
        conn = self._get_conn()
        conn.execute(
            """
            UPDATE queue_leases
            SET lease_status = 'released', metadata_json = ?, updated_at = ?
            WHERE lease_id = ?
            """,
            (self._json(payload), now, normalized_lease_id),
        )
        if lease["task_id"] and self.get_task(lease["task_id"]):
            conn.execute(
                "UPDATE tasks SET lease_expires_at = '', heartbeat_at = ?, updated_at = ? WHERE task_id = ?",
                (now, now, lease["task_id"]),
            )
        conn.commit()
        if lease["task_id"] and self.get_task(lease["task_id"]):
            self.add_event(
                lease["task_id"],
                event_type="queue_lease_released",
                status="",
                message=f"Queue lease {normalized_lease_id} released",
                principal=principal,
                request_id=request_id,
                run_id=run_id,
                payload_ref=normalized_lease_id,
            )
        return self.get_queue_lease(normalized_lease_id, include_inactive=True)  # type: ignore[return-value]

    def report_queue_lease_result(
        self,
        lease_id: str,
        *,
        success: bool,
        result: Optional[dict] = None,
        summary: str = "",
        transient: bool = False,
        retry_after_s: int = 0,
        error: str = "",
        principal: str = "anonymous",
        request_id: str = "",
        run_id: str = "",
        node_id: str = "",
        worker_id: str = "",
        idempotency_key: str = "",
        metadata: Optional[dict] = None,
        fencing_token: str = "",
    ) -> dict:
        normalized_lease_id = str(lease_id or "").strip()
        if not normalized_lease_id:
            raise ValueError("Missing lease_id")
        normalized_node_id = str(node_id or "").strip()
        normalized_worker_id = str(worker_id or "").strip().lower()
        normalized_idempotency_key = str(idempotency_key or "").strip()
        normalized_fencing_token = str(fencing_token or "").strip()
        report_payload = {
            "success": bool(success),
            "transient": bool(transient),
            "retry_after_s": int(retry_after_s or 0),
            "summary": str(summary or ""),
            "error": str(error or ""),
            "result": result if isinstance(result, dict) else {},
            "node_id": normalized_node_id,
            "worker_id": normalized_worker_id,
            "fencing_token": normalized_fencing_token,
        }
        payload_hash = self._json_hash(report_payload)
        report_metadata = dict(metadata or {})
        report_metadata["report"] = {
            "success": bool(success),
            "transient": bool(transient),
            "retry_scheduled": bool(transient and not success),
            "idempotency_key": normalized_idempotency_key,
            "reported_by_node_id": normalized_node_id,
            "reported_by_worker_id": normalized_worker_id,
            "reported_by_principal": principal,
            "reported_request_id": request_id,
            "fencing_token": normalized_fencing_token,
            "payload_hash": payload_hash,
        }
        now = self._now()
        conn = self._get_conn()
        began = False
        try:
            conn.execute("BEGIN IMMEDIATE")
            began = True
            conn.execute(
                """
                UPDATE queue_leases
                SET lease_status = 'expired', updated_at = ?
                WHERE lease_status = 'active'
                  AND lease_expires_at != ''
                  AND lease_expires_at <= ?
                """,
                (now, now),
            )
            lease_row = conn.execute(
                "SELECT * FROM queue_leases WHERE lease_id = ?",
                (normalized_lease_id,),
            ).fetchone()
            if not lease_row:
                conn.rollback()
                raise ValueError(f"Unknown queue lease '{lease_id}'.")
            lease = self._row_to_queue_lease(lease_row).to_dict()
            lease_metadata = dict(lease.get("metadata") or {})
            if normalized_node_id and normalized_node_id != str(lease.get("node_id", "")):
                conn.rollback()
                raise ValueError(f"Queue lease '{lease_id}' is not assigned to node '{normalized_node_id}'.")
            lease_node = self.get_worker_node(str(lease.get("node_id", "") or ""))
            if normalized_worker_id and lease_node and normalized_worker_id != str(lease_node.get("worker_id", "")).lower():
                conn.rollback()
                raise ValueError(f"Queue lease '{lease_id}' is not assigned to worker '{normalized_worker_id}'.")
            lease_fencing_token = str(lease_metadata.get("fencing_token", "") or "")
            if normalized_fencing_token and lease_fencing_token and normalized_fencing_token != lease_fencing_token:
                conn.rollback()
                raise ValueError(f"Queue lease '{lease_id}' rejected a stale fencing token.")

            if normalized_idempotency_key:
                receipt_row = conn.execute(
                    """
                    SELECT payload_hash
                    FROM queue_lease_receipts
                    WHERE lease_id = ? AND idempotency_key = ?
                    """,
                    (normalized_lease_id, normalized_idempotency_key),
                ).fetchone()
                if receipt_row:
                    stored_hash = str(receipt_row["payload_hash"] or "")
                    if stored_hash != payload_hash:
                        conn.rollback()
                        raise ValueError(f"Queue lease '{lease_id}' was already reported with a different payload for this idempotency key.")
                    task_id = str(lease.get("task_id", "") or "").strip()
                    task = self.get_task(task_id) if task_id else None
                    conn.commit()
                    return {
                        "reported": True,
                        "idempotent": True,
                        "retry_scheduled": bool((lease_metadata.get("report") or {}).get("retry_scheduled", False)),
                        "task": task.to_dict() if task else None,
                        "lease": self.get_queue_lease(normalized_lease_id, include_inactive=True),
                        "summary": self.queue_summary(),
                    }

            if lease["lease_status"] != "active":
                previous_report = dict(lease_metadata.get("report") or {})
                previous_key = str(previous_report.get("idempotency_key", "") or "")
                if normalized_idempotency_key and previous_key and normalized_idempotency_key != previous_key:
                    conn.rollback()
                    raise ValueError(f"Queue lease '{lease_id}' was already reported with a different idempotency key.")
                task_id = str(lease.get("task_id", "") or "").strip()
                task = self.get_task(task_id) if task_id else None
                conn.commit()
                if previous_report:
                    return {
                        "reported": True,
                        "idempotent": True,
                        "retry_scheduled": bool(previous_report.get("retry_scheduled", False)),
                        "task": task.to_dict() if task else None,
                        "lease": self.get_queue_lease(normalized_lease_id, include_inactive=True),
                        "summary": self.queue_summary(),
                    }
                raise ValueError(f"Queue lease '{lease_id}' is {lease['lease_status']}.")

            if normalized_idempotency_key:
                conn.execute(
                    """
                    INSERT INTO queue_lease_receipts (
                        receipt_id, lease_id, idempotency_key, payload_hash,
                        response_json, created_at, updated_at
                    )
                    VALUES (?,?,?,?,?,?,?)
                    """,
                    (
                        self._new_id("receipt"),
                        normalized_lease_id,
                        normalized_idempotency_key,
                        payload_hash,
                        "{}",
                        now,
                        now,
                    ),
                )

            task_id = str(lease.get("task_id", "") or "").strip()
            task = self.get_task(task_id) if task_id else None
            merged_lease_metadata = dict(lease_metadata)
            merged_lease_metadata.update(report_metadata)

            if not task:
                conn.execute(
                    """
                    UPDATE queue_leases
                    SET lease_status = 'released', metadata_json = ?, updated_at = ?
                    WHERE lease_id = ?
                    """,
                    (self._json(merged_lease_metadata), now, normalized_lease_id),
                )
                conn.commit()
                return {
                    "reported": True,
                    "task": None,
                    "lease": self.get_queue_lease(normalized_lease_id, include_inactive=True),
                    "summary": self.queue_summary(),
                }

            if transient and not success:
                retry_delay = max(1, int(retry_after_s or task.retry_after_s or 1))
                retry_result = dict(result or {})
                if error:
                    retry_result.setdefault("error", error)
                conn.execute(
                    """
                    UPDATE tasks
                    SET status = ?, summary = ?, principal = ?, request_id = ?, run_id = ?,
                        delegation_status = ?, lease_expires_at = '', heartbeat_at = '',
                        retry_after_s = ?, result_json = ?, updated_at = ?, ended_at = ''
                    WHERE task_id = ?
                    """,
                    (
                        TASK_STATUS_QUEUED,
                        summary or error or "Transient worker failure; retry scheduled",
                        principal or "anonymous",
                        request_id or "",
                        run_id or "",
                        DELEGATION_STATUS_DELEGATED,
                        retry_delay,
                        self._json(retry_result),
                        now,
                        task.task_id,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO task_events (
                        event_id, task_id, event_type, status, message, principal,
                        request_id, run_id, payload_ref, created_at
                    )
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        self._new_id("evt"),
                        task.task_id,
                        "queue_lease_retry_scheduled",
                        TASK_STATUS_QUEUED,
                        summary or error or f"Retry scheduled after {retry_delay}s",
                        principal or "anonymous",
                        request_id or "",
                        run_id or "",
                        normalized_lease_id,
                        now,
                    ),
                )
                conn.execute(
                    """
                    UPDATE queue_leases
                    SET lease_status = 'released', metadata_json = ?, updated_at = ?
                    WHERE lease_id = ?
                    """,
                    (self._json(merged_lease_metadata), now, normalized_lease_id),
                )
                conn.commit()
                return {
                    "reported": True,
                    "retry_scheduled": True,
                    "idempotent": False,
                    "retry_after_s": retry_delay,
                    "task": self.get_task(task.task_id).to_dict() if self.get_task(task.task_id) else None,
                    "lease": self.get_queue_lease(normalized_lease_id, include_inactive=True),
                    "summary": self.queue_summary(),
                }

            status = TASK_STATUS_COMPLETED if success else TASK_STATUS_FAILED
            event_type = "queue_lease_task_completed" if success else "queue_lease_task_failed"
            payload = dict(result or {})
            if error:
                payload.setdefault("error", error)
            conn.execute(
                """
                UPDATE tasks
                SET status = ?, summary = ?, principal = ?, request_id = ?, run_id = ?,
                    result_json = ?, updated_at = ?, ended_at = ?, lease_expires_at = '', heartbeat_at = ?
                WHERE task_id = ?
                """,
                (
                    status,
                    summary or error or status,
                    principal or "anonymous",
                    request_id or "",
                    run_id or "",
                    self._json(payload),
                    now,
                    now,
                    now,
                    task.task_id,
                ),
            )
            conn.execute(
                """
                INSERT INTO task_events (
                    event_id, task_id, event_type, status, message, principal,
                    request_id, run_id, payload_ref, created_at
                )
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    self._new_id("evt"),
                    task.task_id,
                    event_type,
                    status,
                    summary or error or status,
                    principal or "anonymous",
                    request_id or "",
                    run_id or "",
                    normalized_lease_id,
                    now,
                ),
            )
            conn.execute(
                """
                UPDATE queue_leases
                SET lease_status = 'released', metadata_json = ?, updated_at = ?
                WHERE lease_id = ?
                """,
                (self._json(merged_lease_metadata), now, normalized_lease_id),
            )
            conn.commit()
            updated = self.get_task(task.task_id)
            return {
                "reported": True,
                "retry_scheduled": False,
                "idempotent": False,
                "task": updated.to_dict() if updated else None,
                "lease": self.get_queue_lease(normalized_lease_id, include_inactive=True),
                "summary": self.queue_summary(),
            }
        except Exception:
            if began:
                conn.rollback()
            raise

    def get_queue_lease(self, lease_id: str, *, include_inactive: bool = False) -> Optional[dict]:
        now = self._now()
        conditions = ["lease_id = ?"]
        params: list[Any] = [str(lease_id or "").strip()]
        if not include_inactive:
            conditions.append("lease_status = 'active'")
            conditions.append("(lease_expires_at = '' OR lease_expires_at > ?)")
            params.append(now)
        row = self._fetchone(
            f"SELECT * FROM queue_leases WHERE {' AND '.join(conditions)}",
            tuple(params),
        )
        return self._row_to_queue_lease(row).to_dict() if row else None

    def list_queue_leases(
        self,
        *,
        queue_name: str = "",
        node_id: str = "",
        task_id: str = "",
        include_released: bool = False,
        include_expired: bool = False,
        limit: int = 100,
    ) -> list[dict]:
        now = self._now()
        conditions: list[str] = []
        params: list[Any] = []
        if queue_name:
            conditions.append("queue_name = ?")
            params.append(str(queue_name).strip())
        if node_id:
            conditions.append("node_id = ?")
            params.append(str(node_id).strip())
        if task_id:
            conditions.append("task_id = ?")
            params.append(str(task_id).strip())
        status_conditions = ["(lease_status = 'active' AND (lease_expires_at = '' OR lease_expires_at > ?))"]
        params.append(now)
        if include_released:
            status_conditions.append("lease_status = 'released'")
        if include_expired:
            status_conditions.append("lease_status = 'expired'")
            status_conditions.append("(lease_status = 'active' AND lease_expires_at != '' AND lease_expires_at <= ?)")
            params.append(now)
        conditions.append(f"({' OR '.join(status_conditions)})")
        params.append(max(1, min(int(limit or 100), 500)))
        where = f"WHERE {' AND '.join(conditions)}"
        rows = self._fetchall(
            f"SELECT * FROM queue_leases {where} ORDER BY updated_at DESC, lease_id ASC LIMIT ?",
            tuple(params),
        )
        return [self._row_to_queue_lease(row).to_dict() for row in rows]

    def queue_summary(self) -> dict:
        now = self._now()
        queues = self.list_execution_queues()
        rows = self._fetchall(
            """
            SELECT
                CASE
                    WHEN lease_status = 'active' AND lease_expires_at != '' AND lease_expires_at <= ?
                    THEN 'expired'
                    ELSE lease_status
                END AS effective_status,
                COUNT(*) AS cnt
            FROM queue_leases
            GROUP BY effective_status
            """,
            (now,),
        )
        by_status = {str(row["effective_status"]): int(row["cnt"]) for row in rows}
        lease_count = sum(by_status.values())
        queue_depths = {
            row["queue_name"]: int(row["cnt"])
            for row in self._fetchall(
                """
                SELECT queue_name, COUNT(*) AS cnt
                FROM queue_leases
                WHERE lease_status = 'active'
                  AND (lease_expires_at = '' OR lease_expires_at > ?)
                GROUP BY queue_name
                """,
                (now,),
            )
        }
        return {
            "available": True,
            "queue_count": len(queues),
            "lease_count": lease_count,
            "active_lease_count": by_status.get("active", 0),
            "released_lease_count": by_status.get("released", 0),
            "expired_lease_count": by_status.get("expired", 0),
            "by_status": dict(sorted(by_status.items())),
            "queue_depths": dict(sorted(queue_depths.items())),
            "queues": queues,
        }

    def record_dispatch_event(
        self,
        *,
        parent_task_id: str,
        child_task_id: str,
        worker_id: str,
        queue_name: str = "",
        dispatch_status: str = "queued",
        command_text: str = "",
        metadata: Optional[dict] = None,
    ) -> dict:
        dispatch_id = self._new_id("dispatch")
        now = self._now()
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO dispatch_events (
                dispatch_id, parent_task_id, child_task_id, worker_id, queue_name,
                dispatch_status, command_text, metadata_json, created_at, updated_at
            )
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                dispatch_id,
                str(parent_task_id or "").strip(),
                str(child_task_id or "").strip(),
                str(worker_id or "").strip(),
                str(queue_name or "").strip(),
                str(dispatch_status or "queued").strip() or "queued",
                str(command_text or "").strip(),
                self._json(metadata),
                now,
                now,
            ),
        )
        conn.commit()
        return self.get_dispatch_event(dispatch_id) or {"dispatch_id": dispatch_id}

    def get_dispatch_event(self, dispatch_id: str) -> Optional[dict]:
        row = self._fetchone(
            "SELECT * FROM dispatch_events WHERE dispatch_id = ?",
            (str(dispatch_id or "").strip(),),
        )
        if not row:
            return None
        return {
            "dispatch_id": row["dispatch_id"],
            "parent_task_id": row["parent_task_id"],
            "child_task_id": row["child_task_id"],
            "worker_id": row["worker_id"],
            "queue_name": row["queue_name"],
            "dispatch_status": row["dispatch_status"],
            "command_text": row["command_text"],
            "metadata": _safe_json_loads(row["metadata_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_dispatch_events(
        self,
        *,
        parent_task_id: str = "",
        child_task_id: str = "",
        worker_id: str = "",
        limit: int = 100,
    ) -> list[dict]:
        conditions: list[str] = []
        params: list[Any] = []
        if parent_task_id:
            conditions.append("parent_task_id = ?")
            params.append(str(parent_task_id).strip())
        if child_task_id:
            conditions.append("child_task_id = ?")
            params.append(str(child_task_id).strip())
        if worker_id:
            conditions.append("worker_id = ?")
            params.append(str(worker_id).strip())
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(max(1, min(int(limit or 100), 500)))
        rows = self._fetchall(
            f"SELECT * FROM dispatch_events {where} ORDER BY created_at ASC, dispatch_id ASC LIMIT ?",
            tuple(params),
        )
        return [
            {
                "dispatch_id": row["dispatch_id"],
                "parent_task_id": row["parent_task_id"],
                "child_task_id": row["child_task_id"],
                "worker_id": row["worker_id"],
                "queue_name": row["queue_name"],
                "dispatch_status": row["dispatch_status"],
                "command_text": row["command_text"],
                "metadata": _safe_json_loads(row["metadata_json"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def _get_worker_node_record(self, node_id: str) -> Optional[WorkerNodeRecord]:
        row = self._fetchone("SELECT * FROM worker_nodes WHERE node_id = ?", (str(node_id or "").strip(),))
        return self._row_to_worker_node(row) if row else None

    def _worker_capabilities(self, node_ids: list[str]) -> dict[str, list[str]]:
        normalized_ids = [str(item or "").strip() for item in node_ids if str(item or "").strip()]
        if not normalized_ids:
            return {}
        placeholders = ",".join("?" for _ in normalized_ids)
        rows = self._fetchall(
            f"SELECT node_id, capability FROM worker_capabilities WHERE node_id IN ({placeholders}) ORDER BY capability ASC",
            tuple(normalized_ids),
        )
        mapping: dict[str, list[str]] = {}
        for row in rows:
            mapping.setdefault(row["node_id"], []).append(row["capability"])
        return mapping

    def _queue_lookup(self, queue_names: list[str]) -> dict[str, dict]:
        normalized_names = [str(item or "").strip() for item in queue_names if str(item or "").strip()]
        if not normalized_names:
            return {}
        placeholders = ",".join("?" for _ in normalized_names)
        rows = self._fetchall(
            f"SELECT * FROM execution_queues WHERE queue_name IN ({placeholders}) ORDER BY queue_name ASC",
            tuple(normalized_names),
        )
        return {
            row["queue_name"]: self._row_to_execution_queue(row).to_dict()
            for row in rows
        }

    def add_event(
        self,
        task_id: str,
        *,
        event_type: str,
        status: str = "",
        message: str = "",
        principal: str = "anonymous",
        request_id: str = "",
        run_id: str = "",
        payload_ref: str = "",
    ) -> TaskEventRecord:
        event_id = self._new_id("evt")
        created_at = self._now()
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO task_events (event_id, task_id, event_type, status, message, principal, request_id, run_id, payload_ref, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                event_id,
                task_id,
                event_type,
                status,
                message or "",
                principal or "anonymous",
                request_id or "",
                run_id or "",
                payload_ref or "",
                created_at,
            ),
        )
        conn.commit()
        return self.get_events(task_id, limit=1)[0]

    def add_artifact_file(
        self,
        task_id: str,
        *,
        category: str,
        label: str,
        file_path: str,
        media_type: str = "application/octet-stream",
        allow_external_source: bool = False,
    ) -> TaskArtifactRecord:
        artifact_id = self._new_id("art")
        stored_path = self._artifact_storage_path(
            task_id,
            category=category,
            source_path=Path(file_path),
            artifact_id=artifact_id,
            allow_external_source=allow_external_source,
        )
        data = stored_path.read_bytes()
        created_at = self._now()
        sha256 = hashlib.sha256(data).hexdigest()
        size_bytes = len(data)
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO task_artifacts (artifact_id, task_id, category, label, file_path, media_type, size_bytes, sha256, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (
                artifact_id,
                task_id,
                category,
                label,
                str(stored_path),
                media_type,
                size_bytes,
                sha256,
                created_at,
            ),
        )
        conn.commit()
        self.add_event(
            task_id,
            event_type="artifact_added",
            status="ok",
            message=label,
            payload_ref=str(stored_path),
        )
        return self.get_artifacts(task_id, limit=1)[0]

    def write_artifact(
        self,
        task_id: str,
        *,
        category: str,
        label: str,
        filename: str,
        content: str | bytes,
        media_type: str = "application/octet-stream",
    ) -> TaskArtifactRecord:
        target = self._artifact_target_path(task_id, filename)
        if isinstance(content, str):
            target.write_text(content, encoding="utf-8")
        else:
            target.write_bytes(content)
        return self.add_artifact_file(
            task_id,
            category=category,
            label=label,
            file_path=str(target),
            media_type=media_type,
        )

    def add_artifact(
        self,
        task_id: str,
        *,
        category: str,
        label: str,
        content: str | bytes,
        media_type: str = "application/octet-stream",
        suffix: str = "",
    ) -> TaskArtifactRecord:
        safe_suffix = suffix if suffix.startswith(".") else (f".{suffix}" if suffix else "")
        filename = f"{category}_{self._new_id('payload')}{safe_suffix}"
        return self.write_artifact(
            task_id,
            category=category,
            label=label,
            filename=filename,
            content=content,
            media_type=media_type,
        )

    def link_artifacts_from_task(
        self,
        target_task_id: str,
        source_task_id: str,
        *,
        category_prefix: str = "merged",
        label_prefix: str = "Merged child artifact",
        limit: int = 100,
    ) -> List[TaskArtifactRecord]:
        source_artifacts = self.get_artifacts(source_task_id, limit=limit)
        if not source_artifacts:
            return []
        existing_keys = {
            (artifact.file_path, artifact.category, artifact.label)
            for artifact in self.get_artifacts(target_task_id, limit=limit * 2)
        }
        linked: list[TaskArtifactRecord] = []
        for artifact in source_artifacts:
            category = f"{category_prefix}:{artifact.category}" if category_prefix else artifact.category
            label = f"{label_prefix} [{source_task_id[:8]}] {artifact.label}"
            key = (artifact.file_path, category, label)
            if key in existing_keys:
                continue
            linked_artifact = self.add_artifact_file(
                target_task_id,
                category=category,
                label=label,
                file_path=artifact.file_path,
                media_type=artifact.media_type,
            )
            linked.append(linked_artifact)
            existing_keys.add(key)
        return linked

    def get_task(self, task_id: str) -> Optional[TaskRecord]:
        row = self._fetchone("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
        return self._row_to_task(row) if row else None

    def list_tasks(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        status: Optional[str] = None,
        principal: Optional[str] = None,
        request_id: Optional[str] = None,
        root_task_id: Optional[str] = None,
        delegated_to_worker: Optional[str] = None,
        blocked_by_task_id: Optional[str] = None,
        delegation_status: Optional[str] = None,
        owner: Optional[str] = None,
    ) -> Tuple[List[TaskRecord], int]:
        conditions: list[str] = []
        params: list[Any] = []
        if status:
            conditions.append("status = ?")
            params.append(status)
        if principal:
            conditions.append("principal = ?")
            params.append(principal)
        if request_id:
            conditions.append("request_id = ?")
            params.append(request_id)
        if root_task_id:
            conditions.append("root_task_id = ?")
            params.append(root_task_id)
        if delegated_to_worker:
            conditions.append("delegated_to_worker = ?")
            params.append(delegated_to_worker)
        if blocked_by_task_id:
            conditions.append("blocked_by_task_id = ?")
            params.append(blocked_by_task_id)
        if delegation_status:
            conditions.append("delegation_status = ?")
            params.append(delegation_status)
        if owner:
            conditions.append("owner = ?")
            params.append(owner)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        conn = self._get_conn()
        total = conn.execute(f"SELECT COUNT(*) FROM tasks {where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM tasks {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return [self._row_to_task(row) for row in rows], total

    def get_events(self, task_id: str, *, limit: int = 100) -> List[TaskEventRecord]:
        rows = self._fetchall(
            "SELECT * FROM task_events WHERE task_id = ? ORDER BY created_at ASC LIMIT ?",
            (task_id, limit),
        )
        return [self._row_to_event(row) for row in rows]

    def get_artifacts(self, task_id: str, *, limit: int = 100) -> List[TaskArtifactRecord]:
        rows = self._fetchall(
            "SELECT * FROM task_artifacts WHERE task_id = ? ORDER BY created_at ASC LIMIT ?",
            (task_id, limit),
        )
        return [self._row_to_artifact(row) for row in rows]

    def summary(self) -> dict:
        conn = self._get_conn()
        total = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        rows = conn.execute(
            "SELECT status, COUNT(*) AS cnt FROM tasks GROUP BY status ORDER BY cnt DESC"
        ).fetchall()
        recent = conn.execute(
            "SELECT * FROM tasks ORDER BY created_at DESC LIMIT 5"
        ).fetchall()
        waiting = conn.execute(
            "SELECT * FROM tasks WHERE status = ? ORDER BY updated_at DESC LIMIT 5",
            (TASK_STATUS_WAITING_APPROVAL,),
        ).fetchall()
        latest_row = recent[0] if recent else None
        by_status = {row["status"]: row["cnt"] for row in rows}
        active_count = sum(by_status.get(status, 0) for status in ACTIVE_TASK_STATUSES)
        waiting_approval_count = by_status.get(TASK_STATUS_WAITING_APPROVAL, 0)
        failed_count = by_status.get(TASK_STATUS_FAILED, 0)
        running_count = by_status.get(TASK_STATUS_RUNNING, 0)
        queued_count = by_status.get(TASK_STATUS_QUEUED, 0)
        delegated_rows = conn.execute(
            "SELECT * FROM tasks WHERE delegated_to_worker != '' ORDER BY updated_at DESC LIMIT 5"
        ).fetchall()
        blocked_rows = conn.execute(
            "SELECT * FROM tasks WHERE status IN (?, ?) OR blocked_by_task_id != '' ORDER BY updated_at DESC LIMIT 5",
            (TASK_STATUS_WAITING_APPROVAL, TASK_STATUS_BLOCKED),
        ).fetchall()
        blocked_total = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE status IN (?, ?) OR blocked_by_task_id != ''",
            (TASK_STATUS_WAITING_APPROVAL, TASK_STATUS_BLOCKED),
        ).fetchone()[0]
        delegated_queue_depth = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE delegation_status = ?",
            (DELEGATION_STATUS_DELEGATED,),
        ).fetchone()[0]
        by_blocked_kind_rows = conn.execute(
            "SELECT blocked_kind, COUNT(*) AS cnt FROM tasks WHERE blocked_kind != '' GROUP BY blocked_kind ORDER BY cnt DESC"
        ).fetchall()
        latest = self._row_to_task(latest_row).to_dict() if latest_row else None
        worker_summary = self.worker_summary()
        lease_records = self.list_worker_leases(limit=500)
        stale_leases = [item for item in lease_records if item["is_stale"]]
        recoverable_leases = [item for item in stale_leases if item["is_recoverable"]]
        merge_rows = conn.execute(
            "SELECT task_id, command, result_json FROM tasks WHERE result_json LIKE '%merge%' ORDER BY updated_at DESC"
        ).fetchall()
        merge_review_ready_count = 0
        merge_conflict_task_count = 0
        merge_conflict_total = 0
        merge_actionable_count = 0
        merge_override_task_count = 0
        merge_override_total = 0
        by_resolution_policy: dict[str, int] = {}
        latest_merge_task_id = ""
        latest_merge_resolution_policy = ""
        for row in merge_rows:
            result = _safe_json_loads(row["result_json"])
            if not isinstance(result, dict):
                continue
            merge_result = {}
            if result.get("merge_policy"):
                merge_result = result
            else:
                nested_data = result.get("data", {})
                nested_merge = nested_data.get("merge", {}) if isinstance(nested_data, dict) else {}
                if str(row["command"] or "").strip().lower().startswith("handoff ") and isinstance(nested_merge, dict) and nested_merge.get("merge_policy"):
                    merge_result = nested_merge
            if not merge_result:
                continue
            merge_review_ready_count += 1
            resolution_policy = str(merge_result.get("resolution_policy", "") or "").strip()
            if resolution_policy:
                by_resolution_policy[resolution_policy] = by_resolution_policy.get(resolution_policy, 0) + 1
            conflict_count = int((merge_result.get("merge_resolution", {}) or {}).get("conflict_count", 0) or 0)
            if conflict_count > 0:
                merge_conflict_task_count += 1
                merge_conflict_total += conflict_count
            override_count = int(
                merge_result.get(
                    "override_count",
                    (merge_result.get("merge_resolution", {}) or {}).get("override_count", 0),
                )
                or 0
            )
            if override_count > 0:
                merge_override_task_count += 1
                merge_override_total += override_count
            pending_conflict_keys = merge_result.get(
                "review_pending_keys",
                (merge_result.get("merge_resolution", {}) or {}).get("pending_conflict_keys", []),
            )
            if isinstance(pending_conflict_keys, list) and pending_conflict_keys:
                merge_actionable_count += 1
            if not latest_merge_task_id:
                latest_merge_task_id = str(row["task_id"])
                latest_merge_resolution_policy = resolution_policy
        return {
            "available": True,
            "total": total,
            "by_status": by_status,
            "active_count": active_count,
            "waiting_approval_count": waiting_approval_count,
            "failed_count": failed_count,
            "blocked_count": blocked_total,
            "running_count": running_count,
            "queued_count": queued_count,
            "delegated_count": sum(worker_summary["by_worker"].values()),
            "worker_count": worker_summary["worker_count"],
            "handoff_queue_depth": delegated_queue_depth,
            "stale_lease_count": len(stale_leases),
            "recoverable_lease_count": len(recoverable_leases),
            "merge_review_ready_count": merge_review_ready_count,
            "merge_conflict_task_count": merge_conflict_task_count,
            "merge_conflict_total": merge_conflict_total,
            "merge_actionable_count": merge_actionable_count,
            "merge_override_task_count": merge_override_task_count,
            "merge_override_total": merge_override_total,
            "merge_resolution_policies": by_resolution_policy,
            "latest_merge_task_id": latest_merge_task_id,
            "latest_merge_resolution_policy": latest_merge_resolution_policy,
            "workers": worker_summary["workers"],
            "recent": [self._row_to_task(row).to_dict() for row in recent],
            "waiting_approval": [self._row_to_task(row).to_dict() for row in waiting],
            "blocked": [self._row_to_task(row).to_dict() for row in blocked_rows],
            "delegated": [self._row_to_task(row).to_dict() for row in delegated_rows],
            "stale_leases": stale_leases[:5],
            "recoverable_leases": recoverable_leases[:5],
            "by_worker": worker_summary["by_worker"],
            "by_blocked_kind": {row["blocked_kind"]: row["cnt"] for row in by_blocked_kind_rows},
            "latest": latest,
            "latest_task_id": latest["task_id"] if latest else "",
            "latest_status": latest["status"] if latest else "",
            "worker_nodes": self.worker_node_summary(),
        }

    @staticmethod
    def _row_to_task(row) -> TaskRecord:
        return TaskRecord(
            task_id=row["task_id"],
            root_task_id=row["root_task_id"],
            parent_task_id=row["parent_task_id"],
            kind=row["kind"],
            owner=row["owner"],
            title=row["title"],
            summary=row["summary"],
            status=row["status"],
            principal=row["principal"],
            request_id=row["request_id"],
            run_id=row["run_id"],
            scheduler_task_id=row["scheduler_task_id"],
            command=row["command"],
            delegation_status=row["delegation_status"],
            delegated_to_worker=row["delegated_to_worker"],
            blocked_by_task_id=row["blocked_by_task_id"],
            blocked_kind=row["blocked_kind"],
            blocked_reason=row["blocked_reason"],
            lease_expires_at=row["lease_expires_at"],
            heartbeat_at=row["heartbeat_at"],
            attempt_count=row["attempt_count"],
            retry_after_s=row["retry_after_s"],
            payload_json=row["payload_json"],
            result_json=row["result_json"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
        )

    @staticmethod
    def _row_to_event(row) -> TaskEventRecord:
        return TaskEventRecord(
            event_id=row["event_id"],
            task_id=row["task_id"],
            event_type=row["event_type"],
            status=row["status"],
            message=row["message"],
            principal=row["principal"],
            request_id=row["request_id"],
            run_id=row["run_id"],
            payload_ref=row["payload_ref"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _row_to_artifact(row) -> TaskArtifactRecord:
        return TaskArtifactRecord(
            artifact_id=row["artifact_id"],
            task_id=row["task_id"],
            category=row["category"],
            label=row["label"],
            file_path=row["file_path"],
            media_type=row["media_type"],
            size_bytes=row["size_bytes"],
            sha256=row["sha256"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _row_to_worker_node(row) -> WorkerNodeRecord:
        return WorkerNodeRecord(
            node_id=row["node_id"],
            worker_id=row["worker_id"],
            queue_name=row["queue_name"],
            display_name=row["display_name"],
            endpoint=row["endpoint"],
            node_status=row["node_status"],
            drain_state=row["drain_state"],
            last_seen_ip=row["last_seen_ip"],
            metadata_json=row["metadata_json"],
            capacity_json=row["capacity_json"],
            registered_at=row["registered_at"],
            last_heartbeat_at=row["last_heartbeat_at"],
            heartbeat_expires_at=row["heartbeat_expires_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_execution_queue(row) -> ExecutionQueueRecord:
        return ExecutionQueueRecord(
            queue_name=row["queue_name"],
            worker_id=row["worker_id"],
            queue_status=row["queue_status"],
            lease_ttl_seconds=row["lease_ttl_seconds"],
            max_parallelism=row["max_parallelism"],
            metadata_json=row["metadata_json"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_queue_lease(row) -> QueueLeaseRecord:
        return QueueLeaseRecord(
            lease_id=row["lease_id"],
            queue_name=row["queue_name"],
            task_id=row["task_id"],
            node_id=row["node_id"],
            lease_status=row["lease_status"],
            lease_expires_at=row["lease_expires_at"],
            metadata_json=row["metadata_json"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
