from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from botboy.security_context import SecurityContext


class SecurityContextPersistenceTest(unittest.TestCase):
    def test_round_trip_preserves_security_boundary(self):
        context = SecurityContext(
            principal_id="alice",
            org_id="org-a",
            roles=frozenset({"user", "reviewer"}),
            scopes=frozenset({"task:read"}),
            capabilities=frozenset({"task:resume", "filesystem:read"}),
            auth_source="jwt",
            session_id="session-1",
            request_id="req-1",
            task_id="task-1",
            parent_task_id="root-1",
            approval_id="approval-1",
            approval_scope=frozenset({"task:resume"}),
            approval_expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
            authorization_version="7",
        )

        restored = SecurityContext.from_dict(context.to_dict())

        self.assertEqual(restored, context)
        self.assertTrue(restored.has_capability("task:resume"))
        self.assertEqual(restored.org_id, "org-a")
        self.assertEqual(restored.principal_id, "alice")

    def test_expired_approval_is_rejected(self):
        context = SecurityContext(
            principal_id="alice",
            approval_id="approval-1",
            approval_scope=frozenset({"task:resume"}),
            approval_expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
        )

        self.assertFalse(context.approval_valid("task:resume"))

    def test_approval_scope_is_narrow(self):
        context = SecurityContext(
            principal_id="alice",
            approval_id="approval-1",
            approval_scope=frozenset({"task:resume"}),
            approval_expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
        )

        self.assertTrue(context.approval_valid("task:resume"))
        self.assertFalse(context.approval_valid("filesystem:write"))

    def test_child_restriction_cannot_expand_approval_scope(self):
        context = SecurityContext(
            capabilities=frozenset({"task:resume", "filesystem:read"}),
            approval_id="approval-1",
            approval_scope=frozenset({"task:resume", "filesystem:read"}),
            approval_expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
        )

        child = context.restrict_capabilities({"task:resume"})

        self.assertEqual(child.capabilities, frozenset({"task:resume"}))
        self.assertEqual(child.approval_scope, frozenset({"task:resume"}))
        self.assertFalse(child.approval_valid("filesystem:read"))


if __name__ == "__main__":
    unittest.main()
