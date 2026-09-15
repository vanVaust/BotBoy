import tempfile
import unittest

from botboy.approval_store import ApprovalStore
from botboy.tasks import TaskStore


class ApprovalStoreTests(unittest.TestCase):
    def _setup(self):
        tmp = tempfile.TemporaryDirectory()
        store = TaskStore(db_path=f"{tmp.name}/botboy.db")
        task = store.create_task(
            title="approval test",
            principal="alice",
            org_id="org-a",
            command="write artifact report.txt",
        )
        return tmp, store, task

    def test_approval_is_bound_to_task_principal_org_and_command(self):
        tmp, store, task = self._setup()
        try:
            approvals = ApprovalStore(store)
            issued = approvals.issue(
                task_id=task.task_id,
                principal_id="alice",
                org_id="org-a",
                command=task.command,
                authorization_version=7,
            )
            self.assertTrue(issued["approval_id"])

            self.assertIsNone(
                approvals.consume_if_valid(
                    issued["approval_id"],
                    task_id=task.task_id,
                    principal_id="bob",
                    org_id="org-a",
                    command=task.command,
                    authorization_version=7,
                )
            )
            self.assertIsNone(
                approvals.consume_if_valid(
                    issued["approval_id"],
                    task_id=task.task_id,
                    principal_id="alice",
                    org_id="org-a",
                    command="write artifact other.txt",
                    authorization_version=7,
                )
            )
        finally:
            tmp.cleanup()

    def test_approval_is_single_use(self):
        tmp, store, task = self._setup()
        try:
            approvals = ApprovalStore(store)
            issued = approvals.issue(
                task_id=task.task_id,
                principal_id="alice",
                org_id="org-a",
                command=task.command,
            )
            first = approvals.consume_if_valid(
                issued["approval_id"],
                task_id=task.task_id,
                principal_id="alice",
                org_id="org-a",
                command=task.command,
            )
            second = approvals.consume_if_valid(
                issued["approval_id"],
                task_id=task.task_id,
                principal_id="alice",
                org_id="org-a",
                command=task.command,
            )
            self.assertIsNotNone(first)
            self.assertIsNone(second)
        finally:
            tmp.cleanup()

    def test_expired_approval_is_rejected(self):
        tmp, store, task = self._setup()
        try:
            approvals = ApprovalStore(store)
            issued = approvals.issue(
                task_id=task.task_id,
                principal_id="alice",
                org_id="org-a",
                command=task.command,
            )
            conn = store._get_conn()
            conn.execute(
                "UPDATE task_approvals SET expires_at = ? WHERE approval_id = ?",
                ("2000-01-01T00:00:00+00:00", issued["approval_id"]),
            )
            conn.commit()
            self.assertIsNone(
                approvals.consume_if_valid(
                    issued["approval_id"],
                    task_id=task.task_id,
                    principal_id="alice",
                    org_id="org-a",
                    command=task.command,
                )
            )
        finally:
            tmp.cleanup()

    def test_authorization_version_is_bound(self):
        tmp, store, task = self._setup()
        try:
            approvals = ApprovalStore(store)
            issued = approvals.issue(
                task_id=task.task_id,
                principal_id="alice",
                org_id="org-a",
                command=task.command,
                authorization_version=7,
            )
            self.assertIsNone(
                approvals.consume_if_valid(
                    issued["approval_id"],
                    task_id=task.task_id,
                    principal_id="alice",
                    org_id="org-a",
                    command=task.command,
                    authorization_version=8,
                )
            )
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
