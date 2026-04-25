from __future__ import annotations

from sqlite3 import Connection

from .runner import Migration, apply_migrations


TASK_STORE_MIGRATIONS = (
    Migration(
        version=1,
        name="worker_nodes",
        statements=(
            """
            CREATE TABLE IF NOT EXISTS worker_nodes (
                node_id                TEXT PRIMARY KEY,
                worker_id              TEXT NOT NULL,
                queue_name             TEXT NOT NULL,
                display_name           TEXT NOT NULL DEFAULT '',
                endpoint               TEXT NOT NULL DEFAULT '',
                node_status            TEXT NOT NULL DEFAULT 'ready',
                drain_state            TEXT NOT NULL DEFAULT 'active',
                last_seen_ip           TEXT NOT NULL DEFAULT '',
                metadata_json          TEXT NOT NULL DEFAULT '{}',
                capacity_json          TEXT NOT NULL DEFAULT '{}',
                registered_at          TEXT NOT NULL,
                last_heartbeat_at      TEXT NOT NULL DEFAULT '',
                heartbeat_expires_at   TEXT NOT NULL DEFAULT '',
                updated_at             TEXT NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_worker_nodes_worker_id ON worker_nodes(worker_id)",
            "CREATE INDEX IF NOT EXISTS idx_worker_nodes_queue_name ON worker_nodes(queue_name)",
            "CREATE INDEX IF NOT EXISTS idx_worker_nodes_status ON worker_nodes(node_status)",
            "CREATE INDEX IF NOT EXISTS idx_worker_nodes_drain_state ON worker_nodes(drain_state)",
        ),
    ),
    Migration(
        version=2,
        name="worker_capabilities",
        statements=(
            """
            CREATE TABLE IF NOT EXISTS worker_capabilities (
                node_id       TEXT NOT NULL,
                capability    TEXT NOT NULL,
                created_at    TEXT NOT NULL,
                PRIMARY KEY(node_id, capability),
                FOREIGN KEY(node_id) REFERENCES worker_nodes(node_id)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_worker_capabilities_node_id ON worker_capabilities(node_id)",
        ),
    ),
    Migration(
        version=3,
        name="execution_queues",
        statements=(
            """
            CREATE TABLE IF NOT EXISTS execution_queues (
                queue_name          TEXT PRIMARY KEY,
                worker_id           TEXT NOT NULL,
                queue_status        TEXT NOT NULL DEFAULT 'ready',
                lease_ttl_seconds   INTEGER NOT NULL DEFAULT 900,
                max_parallelism     INTEGER NOT NULL DEFAULT 1,
                metadata_json       TEXT NOT NULL DEFAULT '{}',
                created_at          TEXT NOT NULL,
                updated_at          TEXT NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_execution_queues_worker_id ON execution_queues(worker_id)",
            "CREATE INDEX IF NOT EXISTS idx_execution_queues_status ON execution_queues(queue_status)",
        ),
    ),
    Migration(
        version=4,
        name="queue_leases",
        statements=(
            """
            CREATE TABLE IF NOT EXISTS queue_leases (
                lease_id             TEXT PRIMARY KEY,
                queue_name           TEXT NOT NULL,
                task_id              TEXT NOT NULL DEFAULT '',
                node_id              TEXT NOT NULL DEFAULT '',
                lease_status         TEXT NOT NULL DEFAULT 'open',
                lease_expires_at     TEXT NOT NULL DEFAULT '',
                metadata_json        TEXT NOT NULL DEFAULT '{}',
                created_at           TEXT NOT NULL,
                updated_at           TEXT NOT NULL,
                FOREIGN KEY(queue_name) REFERENCES execution_queues(queue_name),
                FOREIGN KEY(node_id) REFERENCES worker_nodes(node_id)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_queue_leases_queue_name ON queue_leases(queue_name)",
            "CREATE INDEX IF NOT EXISTS idx_queue_leases_node_id ON queue_leases(node_id)",
            "CREATE INDEX IF NOT EXISTS idx_queue_leases_status ON queue_leases(lease_status)",
        ),
    ),
    Migration(
        version=5,
        name="workflow_ir_persistence",
        statements=(
            """
            CREATE TABLE IF NOT EXISTS workflow_runs (
                workflow_id       TEXT PRIMARY KEY,
                task_id           TEXT NOT NULL DEFAULT '',
                request_id        TEXT NOT NULL DEFAULT '',
                principal         TEXT NOT NULL DEFAULT 'anonymous',
                source            TEXT NOT NULL DEFAULT 'runtime',
                status            TEXT NOT NULL DEFAULT '',
                goal              TEXT NOT NULL DEFAULT '',
                version           TEXT NOT NULL DEFAULT 'workflow-ir/v1',
                metadata_json     TEXT NOT NULL DEFAULT '{}',
                workflow_json     TEXT NOT NULL DEFAULT '{}',
                created_at        TEXT NOT NULL,
                updated_at        TEXT NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_workflow_runs_task_id ON workflow_runs(task_id)",
            "CREATE INDEX IF NOT EXISTS idx_workflow_runs_request_id ON workflow_runs(request_id)",
            "CREATE INDEX IF NOT EXISTS idx_workflow_runs_principal ON workflow_runs(principal)",
            """
            CREATE TABLE IF NOT EXISTS workflow_policy_decisions (
                decision_id        TEXT PRIMARY KEY,
                workflow_id        TEXT NOT NULL,
                step_id            TEXT NOT NULL DEFAULT '',
                surface            TEXT NOT NULL DEFAULT '',
                action             TEXT NOT NULL DEFAULT '',
                principal          TEXT NOT NULL DEFAULT 'anonymous',
                allowed            INTEGER NOT NULL DEFAULT 0,
                approval_required  INTEGER NOT NULL DEFAULT 0,
                approval_granted   INTEGER NOT NULL DEFAULT 0,
                reason             TEXT NOT NULL DEFAULT '',
                roles_json         TEXT NOT NULL DEFAULT '[]',
                capabilities_json  TEXT NOT NULL DEFAULT '[]',
                evidence_json      TEXT NOT NULL DEFAULT '{}',
                created_at         TEXT NOT NULL,
                FOREIGN KEY(workflow_id) REFERENCES workflow_runs(workflow_id)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_workflow_policy_decisions_workflow_id ON workflow_policy_decisions(workflow_id)",
            "CREATE INDEX IF NOT EXISTS idx_workflow_policy_decisions_allowed ON workflow_policy_decisions(allowed)",
            """
            CREATE TABLE IF NOT EXISTS workflow_replay_events (
                event_id           TEXT PRIMARY KEY,
                workflow_id        TEXT NOT NULL,
                step_id            TEXT NOT NULL DEFAULT '',
                event_type         TEXT NOT NULL DEFAULT '',
                status             TEXT NOT NULL DEFAULT '',
                payload_json       TEXT NOT NULL DEFAULT '{}',
                created_at         TEXT NOT NULL,
                FOREIGN KEY(workflow_id) REFERENCES workflow_runs(workflow_id)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_workflow_replay_events_workflow_id ON workflow_replay_events(workflow_id)",
            "CREATE INDEX IF NOT EXISTS idx_workflow_replay_events_event_type ON workflow_replay_events(event_type)",
        ),
    ),
    Migration(
        version=6,
        name="queue_lease_hardening",
        statements=(
            "CREATE INDEX IF NOT EXISTS idx_queue_leases_queue_status_expiry ON queue_leases(queue_name, lease_status, lease_expires_at)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_queue_leases_active_task_unique ON queue_leases(task_id) WHERE lease_status = 'active' AND task_id != ''",
        ),
    ),
    Migration(
        version=7,
        name="queue_lease_receipts",
        statements=(
            """
            CREATE TABLE IF NOT EXISTS queue_lease_receipts (
                receipt_id           TEXT PRIMARY KEY,
                lease_id             TEXT NOT NULL,
                idempotency_key      TEXT NOT NULL,
                payload_hash         TEXT NOT NULL DEFAULT '',
                response_json        TEXT NOT NULL DEFAULT '{}',
                created_at           TEXT NOT NULL,
                updated_at           TEXT NOT NULL,
                UNIQUE(lease_id, idempotency_key),
                FOREIGN KEY(lease_id) REFERENCES queue_leases(lease_id)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_queue_lease_receipts_lease_id ON queue_lease_receipts(lease_id)",
        ),
    ),
    Migration(
        version=8,
        name="dispatch_events",
        statements=(
            """
            CREATE TABLE IF NOT EXISTS dispatch_events (
                dispatch_id          TEXT PRIMARY KEY,
                parent_task_id       TEXT NOT NULL DEFAULT '',
                child_task_id        TEXT NOT NULL DEFAULT '',
                worker_id            TEXT NOT NULL DEFAULT '',
                queue_name           TEXT NOT NULL DEFAULT '',
                dispatch_status      TEXT NOT NULL DEFAULT 'queued',
                command_text         TEXT NOT NULL DEFAULT '',
                metadata_json        TEXT NOT NULL DEFAULT '{}',
                created_at           TEXT NOT NULL,
                updated_at           TEXT NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_dispatch_events_parent_task_id ON dispatch_events(parent_task_id)",
            "CREATE INDEX IF NOT EXISTS idx_dispatch_events_child_task_id ON dispatch_events(child_task_id)",
            "CREATE INDEX IF NOT EXISTS idx_dispatch_events_worker_id ON dispatch_events(worker_id)",
            "CREATE INDEX IF NOT EXISTS idx_dispatch_events_status ON dispatch_events(dispatch_status)",
        ),
    ),
)


def apply_task_store_migrations(conn: Connection) -> None:
    apply_migrations(conn, "task_store", TASK_STORE_MIGRATIONS)
