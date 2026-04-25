from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from botboy.workflow_ir import PolicyDecisionRecord, WorkflowIR, WorkflowReplayEvent

BEST_EFFORT_PERSISTENCE_ERRORS = (
    AttributeError,
    RuntimeError,
    OSError,
    sqlite3.Error,
    TypeError,
    ValueError,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def _loads(value: str, fallback: Any) -> Any:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback
    return parsed


def persist_workflow_ir_snapshot(
    task_store: Any,
    *,
    workflow_ir: WorkflowIR,
    policy_decisions: Sequence[PolicyDecisionRecord],
    replay_events: Sequence[WorkflowReplayEvent],
    task_id: str = "",
    request_id: str = "",
    principal: str = "anonymous",
    source: str = "runtime",
    status: str = "",
) -> bool:
    """Persist workflow IR records into task-store migration tables."""

    get_conn = getattr(task_store, "_get_conn", None)
    if not callable(get_conn):
        return False
    conn = get_conn()
    now = _utc_now()
    created_at = str(workflow_ir.created_at or now)
    workflow_payload = workflow_ir.to_dict()
    metadata = dict(workflow_ir.metadata or {})
    metadata.setdefault("persisted_at", now)
    try:
        conn.execute(
            """
            INSERT INTO workflow_runs (
                workflow_id, task_id, request_id, principal, source, status, goal, version,
                metadata_json, workflow_json, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(workflow_id) DO UPDATE SET
                task_id=excluded.task_id,
                request_id=excluded.request_id,
                principal=excluded.principal,
                source=excluded.source,
                status=excluded.status,
                goal=excluded.goal,
                version=excluded.version,
                metadata_json=excluded.metadata_json,
                workflow_json=excluded.workflow_json,
                updated_at=excluded.updated_at
            """,
            (
                workflow_ir.workflow_id,
                str(task_id or ""),
                str(request_id or workflow_ir.request_id or ""),
                str(principal or workflow_ir.principal or "anonymous"),
                str(source or "runtime"),
                str(status or ""),
                str(workflow_ir.goal or ""),
                str(workflow_ir.version or "workflow-ir/v1"),
                _json(metadata),
                _json(workflow_payload),
                created_at,
                now,
            ),
        )
        conn.execute(
            "DELETE FROM workflow_policy_decisions WHERE workflow_id = ?",
            (workflow_ir.workflow_id,),
        )
        conn.execute(
            "DELETE FROM workflow_replay_events WHERE workflow_id = ?",
            (workflow_ir.workflow_id,),
        )
        for decision in policy_decisions:
            conn.execute(
                """
                INSERT INTO workflow_policy_decisions (
                    decision_id, workflow_id, step_id, surface, action, principal, allowed,
                    approval_required, approval_granted, reason, roles_json, capabilities_json,
                    evidence_json, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    decision.decision_id,
                    decision.workflow_id,
                    decision.step_id,
                    decision.surface,
                    decision.action,
                    decision.principal,
                    int(bool(decision.allowed)),
                    int(bool(decision.approval_required)),
                    int(bool(decision.approval_granted)),
                    decision.reason,
                    _json(list(decision.roles)),
                    _json(list(decision.capabilities)),
                    _json(dict(decision.evidence)),
                    decision.created_at,
                ),
            )
        for event in replay_events:
            conn.execute(
                """
                INSERT INTO workflow_replay_events (
                    event_id, workflow_id, step_id, event_type, status, payload_json, created_at
                ) VALUES (?,?,?,?,?,?,?)
                """,
                (
                    event.event_id,
                    event.workflow_id,
                    event.step_id,
                    event.event_type,
                    event.status,
                    _json(dict(event.payload)),
                    event.created_at,
                ),
            )
        conn.commit()
        return True
    except BEST_EFFORT_PERSISTENCE_ERRORS:
        return False


def load_workflow_ir_snapshot(task_store: Any, workflow_id: str) -> dict[str, Any] | None:
    """Load a persisted workflow IR snapshot for tests and runtime hooks."""

    get_conn = getattr(task_store, "_get_conn", None)
    if not callable(get_conn):
        return None
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM workflow_runs WHERE workflow_id = ?",
            (workflow_id,),
        ).fetchone()
        if row is None:
            return None
        decision_rows = conn.execute(
            "SELECT * FROM workflow_policy_decisions WHERE workflow_id = ? ORDER BY created_at ASC",
            (workflow_id,),
        ).fetchall()
        event_rows = conn.execute(
            "SELECT * FROM workflow_replay_events WHERE workflow_id = ? ORDER BY created_at ASC",
            (workflow_id,),
        ).fetchall()
        return {
            "workflow_id": row["workflow_id"],
            "task_id": row["task_id"],
            "request_id": row["request_id"],
            "principal": row["principal"],
            "source": row["source"],
            "status": row["status"],
            "goal": row["goal"],
            "version": row["version"],
            "metadata": _loads(row["metadata_json"], {}),
            "workflow_ir": _loads(row["workflow_json"], {}),
            "policy_decisions": [
                {
                    "decision_id": decision_row["decision_id"],
                    "workflow_id": decision_row["workflow_id"],
                    "step_id": decision_row["step_id"],
                    "surface": decision_row["surface"],
                    "action": decision_row["action"],
                    "principal": decision_row["principal"],
                    "allowed": bool(decision_row["allowed"]),
                    "approval_required": bool(decision_row["approval_required"]),
                    "approval_granted": bool(decision_row["approval_granted"]),
                    "reason": decision_row["reason"],
                    "roles": _loads(decision_row["roles_json"], []),
                    "capabilities": _loads(decision_row["capabilities_json"], []),
                    "evidence": _loads(decision_row["evidence_json"], {}),
                    "created_at": decision_row["created_at"],
                }
                for decision_row in decision_rows
            ],
            "replay_events": [
                {
                    "event_id": event_row["event_id"],
                    "workflow_id": event_row["workflow_id"],
                    "step_id": event_row["step_id"],
                    "event_type": event_row["event_type"],
                    "status": event_row["status"],
                    "payload": _loads(event_row["payload_json"], {}),
                    "created_at": event_row["created_at"],
                }
                for event_row in event_rows
            ],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    except BEST_EFFORT_PERSISTENCE_ERRORS:
        return None

