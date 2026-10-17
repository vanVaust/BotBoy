import unittest

from botboy.security_policy import DEFAULT_CAPABILITY_POLICY


class CapabilityPolicyTests(unittest.TestCase):
    def test_user_policy_is_minimal(self):
        self.assertEqual(DEFAULT_CAPABILITY_POLICY.for_roles(frozenset({"user"})), frozenset({"task.read", "task.resume", "task.execute"}))

    def test_worker_and_system_policies_are_explicit(self):
        worker = DEFAULT_CAPABILITY_POLICY.for_roles(frozenset({"worker"}))
        self.assertIn("worker.heartbeat", worker)
        self.assertIn("task.execute", worker)
        self.assertNotIn("worker.drain", worker)
        self.assertEqual(DEFAULT_CAPABILITY_POLICY.for_roles(frozenset({"system"})), DEFAULT_CAPABILITY_POLICY.admin)

    def test_admin_policy_contains_worker_controls(self):
        caps = DEFAULT_CAPABILITY_POLICY.for_roles(frozenset({"admin"}))
        self.assertIn("worker.register", caps)
        self.assertIn("worker.lease", caps)


if __name__ == "__main__":
    unittest.main()
