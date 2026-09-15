import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from botboy.scheduler import BotBoyScheduler, SchedulerStore


class SchedulerSecurityTests(unittest.TestCase):
    def test_server_supplied_security_context_overrides_payload(self):
        scheduler = BotBoyScheduler(":memory:")
        task_id = scheduler.add(
            name="tenant-job",
            schedule="in 1h",
            payload={"_botboy_security": {"principal_id": "attacker", "org_id": "evil"}},
            security_context={"principal_id": "alice", "org_id": "tenant-a"},
        )
        task = scheduler.list_tasks()[0]
        self.assertEqual(task_id, task.task_id)
        self.assertEqual(task.payload["_botboy_security"]["principal_id"], "alice")
        self.assertEqual(task.payload["_botboy_security"]["org_id"], "tenant-a")
        self.assertTrue(task.payload["_botboy_security"]["enforced"])

    def test_expired_approval_is_denied_before_execution(self):
        scheduler = BotBoyScheduler(":memory:")
        expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        scheduler.add(
            name="expired",
            schedule="in 1m",
            security_context={
                "principal_id": "alice",
                "org_id": "tenant-a",
                "approval_expires_at": expired,
            },
        )
        task = scheduler.list_tasks()[0]
        self.assertFalse(scheduler._security_allows(task))

    def test_security_validator_is_called_for_delayed_work(self):
        seen = []
        scheduler = BotBoyScheduler(":memory:", security_validator=lambda task: seen.append(task.task_id) or False)
        scheduler.add(name="validated", schedule="in 1m", security_context={"principal_id": "alice", "org_id": "tenant-a"})
        task = scheduler.list_tasks()[0]
        self.assertFalse(scheduler._security_allows(task))
        self.assertEqual(seen, [task.task_id])

    def test_existing_tenant_jobs_are_migrated_to_enforced_security(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as handle:
            conn = sqlite3.connect(handle.name)
            conn.execute("CREATE TABLE scheduled_tasks (task_id TEXT PRIMARY KEY,name TEXT NOT NULL,schedule TEXT NOT NULL,task_type TEXT NOT NULL DEFAULT 'generic',payload TEXT NOT NULL DEFAULT '{}',next_run_ts REAL NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,max_runs INTEGER,run_count INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL)")
            conn.execute("INSERT INTO scheduled_tasks VALUES (?,?,?,?,?,?,?,?,?,?)", ("legacy", "legacy", "in 1h", "generic", json.dumps({"_botboy_security": {"principal_id": "alice", "org_id": "tenant-a"}}), 9999999999, 1, None, 0, datetime.now(timezone.utc).isoformat()))
            conn.commit()
            conn.close()

            store = SchedulerStore(handle.name)
            task = store.list_tasks()[0]
            self.assertTrue(task.payload["_botboy_security"]["enforced"])
            store.close()


if __name__ == "__main__":
    unittest.main()
