from __future__ import annotations

import argparse
import asyncio
import inspect
import os
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from botboy.core.config import BotBoyConfig
from botboy.tasks import (
    LEASE_TTL_SECONDS,
    TASK_STATUS_RUNNING,
    TaskRecord,
)
from botboy.worker_client import TaskStoreWorkerClient


class WorkerRetryableError(RuntimeError):
    """Raised by an executor when the task should be retried with backoff."""


@dataclass(frozen=True)
class WorkerExecutionOutcome:
    success: bool
    output: str = ""
    data: dict[str, Any] | None = None
    retryable: bool = False
    retry_after_s: int = 0
    error: str = ""


@dataclass(frozen=True)
class WorkerDaemonConfig:
    worker_id: str = "executor"
    node_id: str = ""
    queue_name: str = ""
    target_task_id: str = ""
    principal: str = "worker-daemon"
    poll_interval_s: float = 1.0
    idle_backoff_initial_s: float = 0.25
    idle_backoff_max_s: float = 5.0
    retry_backoff_base_s: float = 2.0
    retry_backoff_max_s: float = 60.0
    heartbeat_interval_s: float = 10.0
    lease_ttl_seconds: int = LEASE_TTL_SECONDS
    max_attempts: int = 3
    max_cycles: int = 0
    once: bool = False
    display_name: str = ""
    endpoint: str = ""
    last_seen_ip: str = ""
    max_parallelism: int = 1


@dataclass(frozen=True)
class WorkerDaemonResult:
    processed: int = 0
    retried: int = 0
    failed: int = 0
    recovered: int = 0
    released: int = 0
    idle_cycles: int = 0


def _normalize_executor_result(value: Any) -> WorkerExecutionOutcome:
    if isinstance(value, WorkerExecutionOutcome):
        return value
    if isinstance(value, dict):
        data = value.get("data")
        return WorkerExecutionOutcome(
            success=bool(value.get("success", False)),
            output=str(value.get("output", "") or ""),
            data=data if isinstance(data, dict) else {},
            retryable=bool(value.get("retryable", False)),
            retry_after_s=max(0, int(value.get("retry_after_s", 0) or 0)),
            error=str(value.get("error", "") or ""),
        )
    if isinstance(value, str):
        return WorkerExecutionOutcome(success=True, output=value)
    if value is None:
        return WorkerExecutionOutcome(success=True)
    return WorkerExecutionOutcome(success=True, data={"value": value})


class WorkerDaemon:
    def __init__(
        self,
        client: TaskStoreWorkerClient,
        *,
        executor: Callable[[TaskRecord], Any],
        config: WorkerDaemonConfig,
        sleeper: Callable[[float], None] = time.sleep,
        on_task_finalized: Optional[Callable[[TaskRecord], None]] = None,
    ) -> None:
        self.client = client
        self.executor = executor
        self.config = config
        self._sleep = sleeper
        self._stop_event = threading.Event()
        self._on_task_finalized = on_task_finalized

    @property
    def stop_requested(self) -> bool:
        return self._stop_event.is_set()

    def stop(self) -> None:
        self._stop_event.set()

    def _retry_backoff(self, attempt_count: int) -> int:
        attempt = max(1, int(attempt_count or 1))
        delay = float(self.config.retry_backoff_base_s) * (2 ** (attempt - 1))
        return max(1, int(min(self.config.retry_backoff_max_s, delay)))

    def _idle_backoff(self, idle_cycles: int) -> float:
        delay = float(self.config.idle_backoff_initial_s) * (2 ** max(0, idle_cycles - 1))
        return max(0.05, min(self.config.idle_backoff_max_s, delay))

    def _ensure_node(self) -> dict:
        return self.client.bootstrap_node(
            display_name=self.config.display_name or f"{self.config.worker_id} worker",
            endpoint=self.config.endpoint,
            last_seen_ip=self.config.last_seen_ip or socket.gethostname(),
            node_status="ready",
        )

    def _run_executor(self, task: TaskRecord) -> WorkerExecutionOutcome:
        result = self.executor(task)
        if inspect.isawaitable(result):
            result = asyncio.run(result)
        return _normalize_executor_result(result)

    def _start_lease_heartbeat(
        self,
        lease_id: str,
        *,
        request_id: str,
        fencing_token: str = "",
    ) -> tuple[threading.Event, threading.Thread]:
        stop_event = threading.Event()

        def _renew_loop() -> None:
            while not stop_event.wait(max(0.1, float(self.config.heartbeat_interval_s))):
                try:
                    self.client.renew_task_lease(
                        lease_id,
                        principal=self.config.principal,
                        request_id=request_id,
                        fencing_token=fencing_token,
                    )
                except Exception:
                    break

        thread = threading.Thread(target=_renew_loop, name=f"botboy-worker-{self.config.worker_id}-lease", daemon=True)
        thread.start()
        return stop_event, thread

    def run_once(self) -> WorkerDaemonResult:
        self._ensure_node()
        self.client.heartbeat_node(
            node_status="ready",
            last_seen_ip=self.config.last_seen_ip or socket.gethostname(),
        )
        recovered = self.client.recover_expired_leases(
            principal=self.config.principal,
            request_id=f"worker-daemon-recover-{self.client.node_id}",
            retry_backoff_s=self._retry_backoff(1),
        )
        claim = self.client.claim_next_task_lease(
            principal=self.config.principal,
            request_id=f"worker-daemon-claim-{self.client.node_id}",
            task_id=self.config.target_task_id,
        )
        if not claim:
            return WorkerDaemonResult(recovered=len(recovered), idle_cycles=1)

        task, lease = claim
        request_id = f"worker-daemon-{task.task_id}"
        fencing_token = str((lease.get("metadata") or {}).get("fencing_token", "") or "")
        started = self.client.record_attempt(
            task,
            principal=self.config.principal,
            request_id=request_id,
            run_id=str(lease.get("lease_id", "")),
        )
        effective_task = started or self.client.store.get_task(task.task_id) or task
        stop_event, heartbeat_thread = self._start_lease_heartbeat(
            str(lease.get("lease_id", "")),
            request_id=request_id,
            fencing_token=fencing_token,
        )
        outcome = WorkerExecutionOutcome(success=False, error="executor not run")
        try:
            outcome = self._run_executor(effective_task)
        except WorkerRetryableError as exc:
            outcome = WorkerExecutionOutcome(success=False, retryable=True, error=str(exc))
        except Exception as exc:
            outcome = WorkerExecutionOutcome(success=False, retryable=False, error=str(exc))
        finally:
            stop_event.set()
            heartbeat_thread.join(timeout=2.0)

        if outcome.success:
            current_task = self.client.store.get_task(task.task_id)
            if current_task and current_task.status == TASK_STATUS_RUNNING:
                self.client.report_task_result(
                    str(lease.get("lease_id", "")),
                    success=True,
                    principal=self.config.principal,
                    request_id=request_id,
                    run_id=str(lease.get("lease_id", "")),
                    summary=outcome.output[:256] or task.summary or task.title,
                    result={
                        "success": True,
                        "output": outcome.output,
                        "data": outcome.data or {},
                        "retryable": False,
                    },
                    fencing_token=fencing_token,
                )
            else:
                self.client.release_task_lease(
                    str(lease.get("lease_id", "")),
                    principal=self.config.principal,
                    request_id=request_id,
                    reason="completed",
                    fencing_token=fencing_token,
                )
            finalized = self.client.store.get_task(task.task_id)
            if finalized and callable(self._on_task_finalized):
                self._on_task_finalized(finalized)
            return WorkerDaemonResult(processed=1, recovered=len(recovered), released=1)

        retryable = outcome.retryable and int(effective_task.attempt_count or 0) < int(self.config.max_attempts or 1)
        reason = outcome.error or outcome.output or "task failed"
        if retryable:
            backoff = outcome.retry_after_s or self._retry_backoff(int(effective_task.attempt_count or 1))
            self.client.report_task_result(
                str(lease.get("lease_id", "")),
                success=False,
                run_id=str(lease.get("lease_id", "")),
                principal=self.config.principal,
                request_id=request_id,
                summary=reason,
                transient=True,
                retry_after_s=backoff,
                error=reason,
                result={"success": False, "error": reason, "retryable": True, "retry_after_s": backoff},
                fencing_token=fencing_token,
            )
            finalized = self.client.store.get_task(task.task_id)
            if finalized and callable(self._on_task_finalized):
                self._on_task_finalized(finalized)
            return WorkerDaemonResult(retried=1, recovered=len(recovered), released=1)

        current_task = self.client.store.get_task(task.task_id)
        if current_task and current_task.status == TASK_STATUS_RUNNING:
            self.client.report_task_result(
                str(lease.get("lease_id", "")),
                success=False,
                principal=self.config.principal,
                request_id=request_id,
                run_id=str(lease.get("lease_id", "")),
                summary=reason,
                error=reason,
                result={"success": False, "error": reason, "retryable": False},
                fencing_token=fencing_token,
            )
        else:
            self.client.release_task_lease(
                str(lease.get("lease_id", "")),
                principal=self.config.principal,
                request_id=request_id,
                reason="failed",
                fencing_token=fencing_token,
            )
        finalized = self.client.store.get_task(task.task_id)
        if finalized and callable(self._on_task_finalized):
            self._on_task_finalized(finalized)
        return WorkerDaemonResult(failed=1, recovered=len(recovered), released=1)

    def run(self) -> WorkerDaemonResult:
        result = WorkerDaemonResult()
        idle_cycles = 0
        cycles = 0
        while not self.stop_requested:
            cycle = self.run_once()
            result = WorkerDaemonResult(
                processed=result.processed + cycle.processed,
                retried=result.retried + cycle.retried,
                failed=result.failed + cycle.failed,
                recovered=result.recovered + cycle.recovered,
                released=result.released + cycle.released,
                idle_cycles=result.idle_cycles + cycle.idle_cycles,
            )
            cycles += 1
            if self.config.once:
                break
            if self.config.max_cycles and cycles >= self.config.max_cycles:
                break
            if cycle.processed or cycle.retried or cycle.failed:
                idle_cycles = 0
                self._sleep(float(self.config.poll_interval_s))
                continue
            idle_cycles += 1
            result = WorkerDaemonResult(
                processed=result.processed,
                retried=result.retried,
                failed=result.failed,
                recovered=result.recovered,
                released=result.released,
                idle_cycles=idle_cycles,
            )
            self._sleep(self._idle_backoff(idle_cycles))
        return result


def add_cli_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db-path", default="", help="Optional task-store database path")
    parser.add_argument("--artifact-root", default="", help="Optional task artifact root")
    parser.add_argument("--worker-id", default="executor", help="Worker identifier to execute")
    parser.add_argument("--node-id", default="", help="Worker node identifier")
    parser.add_argument("--queue-name", default="", help="Queue name for the worker node")
    parser.add_argument("--target-task-id", default="", help="Optional queued task id to claim exclusively")
    parser.add_argument("--display-name", default="", help="Display name for node registration")
    parser.add_argument("--endpoint", default="", help="Optional node endpoint")
    parser.add_argument("--poll-interval", type=float, default=1.0, help="Sleep between active polling cycles")
    parser.add_argument("--idle-backoff", type=float, default=0.25, help="Initial backoff when the queue is empty")
    parser.add_argument("--idle-backoff-max", type=float, default=5.0, help="Maximum idle backoff")
    parser.add_argument("--retry-base", type=float, default=2.0, help="Base retry delay for transient failures")
    parser.add_argument("--retry-max", type=float, default=60.0, help="Maximum retry delay")
    parser.add_argument("--heartbeat-interval", type=float, default=10.0, help="Lease heartbeat interval")
    parser.add_argument("--lease-ttl", type=int, default=LEASE_TTL_SECONDS, help="Queue lease TTL in seconds")
    parser.add_argument("--max-attempts", type=int, default=3, help="Maximum delivery attempts per task")
    parser.add_argument("--max-parallelism", type=int, default=1, help="Maximum active leases for the node queue")
    parser.add_argument("--max-cycles", type=int, default=0, help="Optional maximum loop cycles before exit")
    parser.add_argument("--once", action="store_true", help="Run a single polling cycle and exit")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="botboy-worker-daemon", description="BotBoy external worker daemon")
    add_cli_arguments(parser)
    return parser


def _resolve_config(args: argparse.Namespace) -> BotBoyConfig:
    config = BotBoyConfig.load()
    db_path = str(getattr(args, "db_path", "") or "").strip()
    artifact_root = str(getattr(args, "artifact_root", "") or "").strip()
    if db_path:
        config.tasks.db_path = db_path
    if artifact_root:
        config.tasks.artifact_root = artifact_root
    return config


def run_from_namespace(args: argparse.Namespace) -> tuple[int, WorkerDaemonResult]:
    config = _resolve_config(args)
    from botboy import BotBoy

    bot = BotBoy(config)
    if not bot.initialize():
        bot.shutdown()
        raise SystemExit("[BotBoy] Initialisation failed. Check logs.")
    if not bot.task_store:
        bot.shutdown()
        raise SystemExit("[BotBoy] Task store not available.")

    node_id = str(args.node_id or "").strip() or f"{socket.gethostname()}-{os.getpid()}"
    client = TaskStoreWorkerClient(
        bot.task_store,
        worker_id=str(args.worker_id or "").strip() or "executor",
        node_id=node_id,
        queue_name=str(args.queue_name or "").strip(),
        lease_ttl_seconds=int(args.lease_ttl or LEASE_TTL_SECONDS),
        max_parallelism=max(1, int(args.max_parallelism or 1)),
    )

    async def _execute_task(task: TaskRecord) -> Any:
        command = task.command or task.title
        return await bot.process_command(
            command,
            principal="worker-daemon",
            request_id=task.request_id or f"worker-daemon-{task.task_id}",
            task_context=task.to_context(),
        )

    daemon = WorkerDaemon(
        client,
        executor=_execute_task,
        config=WorkerDaemonConfig(
            worker_id=client.worker_id,
            node_id=client.node_id,
            queue_name=client.queue_name,
            target_task_id=str(args.target_task_id or "").strip(),
            principal="worker-daemon",
            poll_interval_s=float(args.poll_interval or 1.0),
            idle_backoff_initial_s=float(args.idle_backoff or 0.25),
            idle_backoff_max_s=float(args.idle_backoff_max or 5.0),
            retry_backoff_base_s=float(args.retry_base or 2.0),
            retry_backoff_max_s=float(args.retry_max or 60.0),
            heartbeat_interval_s=float(args.heartbeat_interval or 10.0),
            lease_ttl_seconds=int(args.lease_ttl or LEASE_TTL_SECONDS),
            max_attempts=max(1, int(args.max_attempts or 1)),
            max_cycles=max(0, int(args.max_cycles or 0)),
            once=bool(args.once),
            display_name=str(args.display_name or "").strip(),
            endpoint=str(args.endpoint or "").strip(),
            last_seen_ip=socket.gethostname(),
            max_parallelism=max(1, int(args.max_parallelism or 1)),
        ),
        on_task_finalized=(
            lambda record: bot._sync_parent_after_child(
                record,
                principal="worker-daemon",
                request_id=record.request_id or f"worker-daemon-{record.task_id}",
            )
        ),
    )
    try:
        result = daemon.run()
    finally:
        bot.shutdown()
    return 0, result


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    exit_code, result = run_from_namespace(args)
    print(
        "Worker daemon finished: "
        f"processed={result.processed} retried={result.retried} failed={result.failed} "
        f"recovered={result.recovered} released={result.released}"
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
