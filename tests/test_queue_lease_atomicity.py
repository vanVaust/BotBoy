import sqlite3
import tempfile
import unittest
from pathlib import Path

from botboy.tasks import TaskStore


class QueueLeaseAtomicityTests(unittest.TestCase):
    def test_database_trigger_rejects_capacity_overflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "tasks.sqlite3")
            store = TaskStore(db_path=db_path)
            conn = store._get_conn()
            now = store._now()
            conn.execute(
                """
                INSERT INTO execution_queues (
                    queue_name, worker_id, queue_status, lease_ttl_seconds,
                    max_parallelism, metadata_json, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?)
                """,
                ("q1", "executor", "ready", 900, 1, "{}", now, now),
            )
            conn.execute(
                """
                INSERT INTO worker_nodes (
                    node_id, worker_id, queue_name, display_name, endpoint,
                    node_status, drain_state, last_seen_ip, metadata_json,
                    capacity_json, registered_at, last_heartbeat_at,
                    heartbeat_expires_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "node-1", "executor", "q1", "Executor", "",
                    "ready", "active", "", "{}", "{}", now, now,
                    store._expires_at(seconds=900), now,
                ),
            )
            conn.execute(
                """
                INSERT INTO queue_leases (
                    lease_id, queue_name, task_id, node_id, lease_status,
                    lease_expires_at, metadata_json, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                ("lease-1", "q1", "", "node-1", "active", store._expires_at(seconds=900), "{}", now, now),
            )
            conn.commit()

            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    """
                    INSERT INTO queue_leases (
                        lease_id, queue_name, task_id, node_id, lease_status,
                        lease_expires_at, metadata_json, created_at, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    ("lease-2", "q1", "", "node-1", "active", store._expires_at(seconds=900), "{}", now, now),
                )
            conn.rollback()
            store.close()

    def test_capacity_allows_release_then_reacquire(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "tasks.sqlite3")
            store = TaskStore(db_path=db_path)
            conn = store._get_conn()
            now = store._now()
            conn.execute(
                "INSERT INTO execution_queues VALUES (?,?,?,?,?,?,?,?)",
                ("q1", "executor", "ready", 900, 1, "{}", now, now),
            )
            conn.execute(
                """
                INSERT INTO worker_nodes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "node-1", "executor", "q1", "Executor", "", "ready", "active", "",
                    "{}", "{}", now, now, store._expires_at(seconds=900), now,
                ),
            )
            conn.execute(
                """
                INSERT INTO queue_leases VALUES (?,?,?,?,?,?,?,?,?)
                """,
                ("lease-1", "q1", "", "node-1", "active", store._expires_at(seconds=900), "{}", now, now),
            )
            conn.commit()
            conn.execute("UPDATE queue_leases SET lease_status='released', updated_at=? WHERE lease_id='lease-1'", (now,))
            conn.commit()
            conn.execute(
                """
                INSERT INTO queue_leases VALUES (?,?,?,?,?,?,?,?,?)
                """,
                ("lease-2", "q1", "", "node-1", "active", store._expires_at(seconds=900), "{}", now, now),
            )
            conn.commit()
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM queue_leases WHERE lease_status='active'").fetchone()[0], 1)
            store.close()


if __name__ == "__main__":
    unittest.main()
