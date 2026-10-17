"""Migration helpers for additive BotBoy schema evolution."""

from sqlite3 import Connection

from .runner import Migration, apply_migrations
from .task_store import apply_task_store_migrations as _apply_task_store_migrations


def apply_task_store_migrations(conn: Connection) -> None:
    """Apply task-store migrations and keep lease-capacity enforcement current."""
    _apply_task_store_migrations(conn)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_queue_leases_active_queue ON queue_leases(queue_name, lease_status)")
    conn.execute("DROP TRIGGER IF EXISTS trg_queue_leases_capacity")
    conn.execute(
        """
        CREATE TRIGGER trg_queue_leases_capacity
        BEFORE INSERT ON queue_leases
        WHEN NEW.lease_status = 'active'
          AND (
              SELECT COUNT(*)
              FROM queue_leases
              WHERE queue_name = NEW.queue_name
                AND lease_status = 'active'
                AND lease_expires_at != ''
                AND replace(lease_expires_at, 'T', ' ') > CURRENT_TIMESTAMP
          ) >= (
              SELECT MAX(1, max_parallelism)
              FROM execution_queues
              WHERE queue_name = NEW.queue_name
          )
        BEGIN
            SELECT RAISE(ABORT, 'queue max_parallelism exceeded');
        END
        """
    )
    conn.commit()


__all__ = ["Migration", "apply_migrations", "apply_task_store_migrations"]
