from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from botboy.tasks import (
    LEASE_TTL_SECONDS,
    TASK_STATUS_FAILED,
    TASK_STATUS_QUEUED,
    TASK_STATUS_RUNNING,
    TaskRecord,
    TaskStore,
)


def _parse_iso(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _task_is_ready(record: TaskRecord, now: Optional[datetime] = None) -> bool:
    if record.status != TASK_STATUS_QUEUED:
        return False
    retry_after_s = max(0, int(record.retry_after_s or 0))
    if retry_after_s <= 0:
        return True
    anchor = _parse_iso(record.updated_at) or _parse_iso(record.created_at)
    if not anchor:
        return True
    current_time = now or datetime.now(timezone.utc)
    return current_time >= anchor + timedelta(seconds=retry_after_s)


@dataclass(frozen=True)
class WorkerLeaseClientConfig:
    worker_id: str
    node_id: str
    queue_name: str
    lease_ttl_seconds: int = LEASE_TTL_SECONDS
    max_parallelism: int = 1


class TaskStoreWorkerClient:
    """Local worker transport backed by TaskStore."""

    def __init__(
        self,
        store: TaskStore,
        *,
        worker_id: str,
        node_id: str,
        queue_name: str = "",
        lease_ttl_seconds: int = LEASE_TTL_SECONDS,
        max_parallelism: int = 1,
    ) -> None:
        self.store = store
        normalized_worker_id = str(worker_id or "").strip().lower() or "executor"
        normalized_node_id = str(node_id or "").strip() or "worker-node"
        normalized_queue_name = str(queue_name or "").strip() or f"{normalized_worker_id}.{normalized_node_id}"
        self.config = WorkerLeaseClientConfig(
            worker_id=normalized_worker_id,
            node_id=normalized_node_id,
            queue_name=normalized_queue_name,
            lease_ttl_seconds=max(30, int(lease_ttl_seconds or LEASE_TTL_SECONDS)),
            max_parallelism=max(1, int(max_parallelism or 1)),
        )

    @property
    def worker_id(self) -> str:
        return self.config.worker_id

    @property
    def node_id(self) -> str:
        return self.config.node_id

    @property
    def queue_name(self) -> str:
        return self.config.queue_name

    def bootstrap_node(
        self,
        *,
        display_name: str = "",
        endpoint: str = "",
        capabilities: Optional[list[str]] = None,
        metadata: Optional[dict] = None,
        last_seen_ip: str = "",
        node_status: str = "ready",
    ) -> dict:
        return self.store.register_worker_node(
            node_id=self.node_id,
            worker_id=self.worker_id,
            display_name=display_name,
            endpoint=endpoint,
            capabilities=capabilities,
            queue_name=self.queue_name,
            lease_ttl_seconds=self.config.lease_ttl_seconds,
            max_parallelism=self.config.max_parallelism,
            metadata=metadata,
            last_seen_ip=last_seen_ip,
            node_status=node_status,
        )

    def heartbeat_node(
        self,
        *,
        node_status: str = "ready",
        metadata: Optional[dict] = None,
        last_seen_ip: str = "",
        health: str = "",
        load: Optional[float] = None,
    ) -> Optional[dict]:
        return self.store.heartbeat_worker_node(
            self.node_id,
            node_status=node_status,
            metadata=metadata,
            last_seen_ip=last_seen_ip,
            health=health,
            load=load,
        )

    def list_pending_tasks(self, *, limit: int = 25) -> list[TaskRecord]:
        records, _total = self.store.list_tasks(
            limit=max(1, min(int(limit or 25), 200)),
            status=TASK_STATUS_QUEUED,
            delegated_to_worker=self.worker_id,
        )
        now = datetime.now(timezone.utc)
        candidates = [record for record in records if _task_is_ready(record, now)]
        candidates.sort(key=lambda record: (record.updated_at, record.task_id))
        return candidates

    def list_running_tasks(self, *, limit: int = 25) -> list[TaskRecord]:
        records, _total = self.store.list_tasks(
            limit=max(1, min(int(limit or 25), 200)),
            status=TASK_STATUS_RUNNING,
            delegated_to_worker=self.worker_id,
        )
        records.sort(key=lambda record: (record.updated_at, record.task_id))
        return records

    def list_node_leases(self, *, limit: int = 100) -> list[dict]:
        return self.store.list_queue_leases(
            node_id=self.node_id,
            include_released=True,
            include_expired=True,
            limit=limit,
        )

    def claim_next_task_lease(
        self,
        *,
        principal: str,
        request_id: str,
        run_id: str = "",
        task_id: str = "",
        limit: int = 50,
    ) -> tuple[TaskRecord, dict] | None:
        claim = self.store.claim_next_queue_lease(
            queue_name=self.queue_name,
            node_id=self.node_id,
            worker_id=self.worker_id,
            task_id=task_id,
            principal=principal,
            request_id=request_id,
            run_id=run_id,
            lease_ttl_seconds=self.config.lease_ttl_seconds,
            limit=limit,
            metadata={"client": "TaskStoreWorkerClient"},
        )
        if not claim:
            return None
        task_payload = claim.get("task") if isinstance(claim, dict) else None
        task_id = str((task_payload or {}).get("task_id", "") if isinstance(task_payload, dict) else "").strip()
        task = self.store.get_task(task_id) if task_id else None
        lease = claim.get("lease") if isinstance(claim, dict) else None
        if not task or not isinstance(lease, dict):
            return None
        return task, lease

    def acquire_task_lease(
        self,
        record: TaskRecord,
        *,
        principal: str,
        request_id: str,
        run_id: str = "",
    ) -> dict:
        return self.store.acquire_queue_lease(
            queue_name=self.queue_name,
            node_id=self.node_id,
            task_id=record.task_id,
            principal=principal,
            request_id=request_id,
            run_id=run_id,
            lease_ttl_seconds=self.config.lease_ttl_seconds,
        )

    def renew_task_lease(
        self,
        lease_id: str,
        *,
        principal: str,
        request_id: str,
        run_id: str = "",
        fencing_token: str = "",
    ) -> dict:
        return self.store.renew_queue_lease(
            lease_id,
            principal=principal,
            request_id=request_id,
            run_id=run_id,
            lease_ttl_seconds=self.config.lease_ttl_seconds,
            fencing_token=fencing_token,
        )

    def release_task_lease(
        self,
        lease_id: str,
        *,
        principal: str,
        request_id: str,
        reason: str = "",
        run_id: str = "",
        fencing_token: str = "",
    ) -> dict:
        return self.store.release_queue_lease(
            lease_id,
            principal=principal,
            request_id=request_id,
            run_id=run_id,
            reason=reason,
            node_id=self.node_id,
            worker_id=self.worker_id,
            fencing_token=fencing_token,
        )

    def report_task_result(
        self,
        lease_id: str,
        *,
        success: bool,
        principal: str,
        request_id: str,
        run_id: str = "",
        result: Optional[dict] = None,
        summary: str = "",
        transient: bool = False,
        retry_after_s: int = 0,
        error: str = "",
        fencing_token: str = "",
    ) -> dict:
        return self.store.report_queue_lease_result(
            lease_id,
            success=success,
            result=result,
            summary=summary,
            transient=transient,
            retry_after_s=retry_after_s,
            error=error,
            principal=principal,
            request_id=request_id,
            run_id=run_id,
            node_id=self.node_id,
            worker_id=self.worker_id,
            metadata={"client": "TaskStoreWorkerClient"},
            fencing_token=fencing_token,
        )

    def record_attempt(
        self,
        task: TaskRecord,
        *,
        principal: str,
        request_id: str,
        run_id: str = "",
    ) -> Optional[TaskRecord]:
        updated = self.store.update_task(
            task.task_id,
            attempt_count=int(task.attempt_count or 0) + 1,
            retry_after_s=0,
            principal=principal,
            request_id=request_id,
            run_id=run_id,
        )
        if updated:
            self.store.add_event(
                task.task_id,
                event_type="worker_attempt_started",
                status=TASK_STATUS_RUNNING,
                message="Worker attempt started",
                principal=principal,
                request_id=request_id,
                run_id=run_id,
            )
        return updated

    def schedule_retry(
        self,
        task: TaskRecord,
        *,
        retry_after_s: int,
        principal: str,
        request_id: str,
        reason: str,
        run_id: str = "",
        result: Optional[dict] = None,
    ) -> Optional[TaskRecord]:
        updated = self.store.update_task(
            task.task_id,
            status=TASK_STATUS_QUEUED,
            summary=reason[:256],
            result=result,
            retry_after_s=max(1, int(retry_after_s or 1)),
            lease_expires_at="",
            heartbeat_at="",
            principal=principal,
            request_id=request_id,
            run_id=run_id,
        )
        if updated:
            self.store.add_event(
                task.task_id,
                event_type="worker_retry_scheduled",
                status=TASK_STATUS_QUEUED,
                message=reason[:256],
                principal=principal,
                request_id=request_id,
                run_id=run_id,
            )
        return updated

    def mark_failed(
        self,
        task: TaskRecord,
        *,
        principal: str,
        request_id: str,
        reason: str,
        run_id: str = "",
        result: Optional[dict] = None,
    ) -> Optional[TaskRecord]:
        updated = self.store.update_task(
            task.task_id,
            status=TASK_STATUS_FAILED,
            summary=reason[:256],
            result=result,
            retry_after_s=0,
            lease_expires_at="",
            heartbeat_at="",
            principal=principal,
            request_id=request_id,
            run_id=run_id,
            ended=True,
        )
        if updated:
            self.store.add_event(
                task.task_id,
                event_type="worker_failed",
                status=TASK_STATUS_FAILED,
                message=reason[:256],
                principal=principal,
                request_id=request_id,
                run_id=run_id,
            )
        return updated

    def recover_expired_leases(
        self,
        *,
        principal: str,
        request_id: str,
        retry_backoff_s: int,
    ) -> list[str]:
        recovered: list[str] = []
        for lease in self.list_node_leases():
            lease_status = str(lease.get("lease_status", "") or "")
            task_id = str(lease.get("task_id", "") or "").strip()
            if not task_id:
                continue
            task = self.store.get_task(task_id)
            if not task:
                continue
            is_expired = bool(lease.get("is_expired")) or lease_status == "expired"
            if task.status in {TASK_STATUS_FAILED, "completed", "cancelled"}:
                if lease_status in {"active", "expired"}:
                    self.release_task_lease(
                        str(lease.get("lease_id", "")),
                        principal=principal,
                        request_id=request_id,
                        reason="finalized task cleanup",
                        fencing_token=str((lease.get("metadata") or {}).get("fencing_token", "") or ""),
                    )
                continue
            if not is_expired:
                continue
            self.store.update_task(
                task.task_id,
                status=TASK_STATUS_QUEUED,
                retry_after_s=max(1, int(retry_backoff_s or 1)),
                lease_expires_at="",
                heartbeat_at="",
                principal=principal,
                request_id=request_id,
            )
            self.store.add_event(
                task.task_id,
                event_type="worker_recovered",
                status=TASK_STATUS_QUEUED,
                message="Recovered expired worker lease",
                principal=principal,
                request_id=request_id,
                run_id="",
            )
            recovered.append(task.task_id)
            if lease_status in {"active", "expired"}:
                self.release_task_lease(
                    str(lease.get("lease_id", "")),
                    principal=principal,
                    request_id=request_id,
                    reason="expired recovery",
                    fencing_token=str((lease.get("metadata") or {}).get("fencing_token", "") or ""),
                )
        return recovered
