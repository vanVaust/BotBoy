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
        ),
    ),
    Migration(
        version=5,
        name="tenancy_support",
        statements=(
            """
            CREATE TABLE IF NOT EXISTS organizations (
                org_id              TEXT PRIMARY KEY,
                name                TEXT NOT NULL,
                billing_tier        TEXT NOT NULL DEFAULT 'free',
                is_active            INTEGER NOT NULL DEFAULT 1,
                metadata_json        TEXT NOT NULL DEFAULT '{}',
                created_at           TEXT NOT NULL,
                updated_at           TEXT NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_organizations_name ON organizations(name)",
            "ALTER TABLE tasks ADD COLUMN org_id TEXT NOT NULL DEFAULT 'default'",
            "CREATE INDEX IF NOT EXISTS idx_tasks_org_id ON tasks(org_id)",
        ),
    ),
    Migration(
        version=9,
        name="replay_diffs",
        statements=(
            """
            CREATE TABLE IF NOT EXISTS replay_diffs (
                report_id             TEXT PRIMARY KEY,
                expected_run_id       TEXT NOT NULL DEFAULT '',
                actual_run_id         TEXT NOT NULL DEFAULT '',
                source                TEXT NOT NULL DEFAULT 'runtime',
                matches               INTEGER NOT NULL DEFAULT 0,
                entry_count           INTEGER NOT NULL DEFAULT 0,
                reason_codes_json     TEXT NOT NULL DEFAULT '[]',
                categories_json       TEXT NOT NULL DEFAULT '[]',
                report_json           TEXT NOT NULL DEFAULT '{}',
                created_at            TEXT NOT NULL,
                updated_at            TEXT NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_replay_diffs_expected ON replay_diffs(expected_run_id)",
            "CREATE INDEX IF NOT EXISTS idx_replay_diffs_actual ON replay_diffs(actual_run_id)",
            "CREATE INDEX IF NOT EXISTS idx_replay_diffs_matches ON replay_diffs(matches)",
        ),
    ),
    Migration(
        version=10,
        name="atomic_queue_lease_capacity",
        statements=(
            "CREATE INDEX IF NOT EXISTS idx_queue_leases_active_queue ON queue_leases(queue_name, lease_status)",
            """
            CREATE TRIGGER IF NOT EXISTS trg_queue_leases_capacity
            BEFORE INSERT ON queue_leases
            WHEN NEW.lease_status = 'active'
              AND (
                  SELECT COUNT(*)
                  FROM queue_leases
                  WHERE queue_name = NEW.queue_name
                    AND lease_status = 'active'
                    AND lease_expires_at != ''
                    AND lease_expires_at > CURRENT_TIMESTAMP
              ) >= (
                  SELECT MAX(1, max_parallelism)
                  FROM execution_queues
                  WHERE queue_name = NEW.queue_name
              )
            BEGIN
                SELECT RAISE(ABORT, 'queue max_parallelism exceeded');
            END
            """,
        ),
    ),
)


def apply_task_store_migrations(conn: Connection) -> None:
    apply_migrations(conn, "task_store", TASK_STORE_MIGRATIONS)
