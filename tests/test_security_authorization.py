import unittest

from botboy.security_authorization import AuthorizationError, authorize_context, intersect_capabilities, require_authorized
from botboy.security_context import SecurityContext


class AuthorizationGateTests(unittest.TestCase):
    def _context(self) -> SecurityContext:
        return SecurityContext(
            principal_id="alice",
            org_id="org-a",
            roles=frozenset({"user"}),
            scopes=frozenset({"task:read"}),
            capabilities=frozenset({"task.read", "task.resume"}),
            auth_source="test",
            task_id="task-1",
            approval_id="approval-1",
            approval_scope="task.resume",
            approval_expires_at="2999-01-01T00:00:00+00:00",
        )

    def test_deny_by_default_without_principal(self):
        ctx = SecurityContext()
        self.assertFalse(authorize_context(ctx).allowed)

    def test_object_and_tenant_binding(self):
        ctx = self._context()
        self.assertTrue(authorize_context(ctx, principal_id="alice", org_id="org-a", task_id="task-1").allowed)
        self.assertFalse(authorize_context(ctx, principal_id="bob").allowed)
        self.assertFalse(authorize_context(ctx, org_id="org-b").allowed)
        self.assertFalse(authorize_context(ctx, task_id="task-2").allowed)

    def test_capability_and_scope(self):
        ctx = self._context()
        self.assertTrue(authorize_context(ctx, required_capability="task.resume").allowed)
        self.assertFalse(authorize_context(ctx, required_capability="worker.register").allowed)
        self.assertTrue(authorize_context(ctx, required_scope="task:read").allowed)
        self.assertFalse(authorize_context(ctx, required_scope="task:write").allowed)

    def test_final_approval_gate(self):
        ctx = self._context()
        self.assertTrue(authorize_context(ctx, required_capability="task.resume", require_approval=True).allowed)
        unapproved = SecurityContext(
            principal_id="alice", org_id="org-a", task_id="task-1",
            capabilities=frozenset({"task.resume"})
        )
        self.assertFalse(authorize_context(unapproved, required_capability="task.resume", require_approval=True).allowed)
        with self.assertRaises(AuthorizationError):
            require_authorized(unapproved, required_capability="task.resume", require_approval=True)

    def test_capability_intersection_never_expands(self):
        self.assertEqual(intersect_capabilities({"a", "b"}, {"b", "c"}, {"b"}), frozenset({"b"}))
        self.assertEqual(intersect_capabilities({"a"}, set()), frozenset())


if __name__ == "__main__":
    unittest.main()
