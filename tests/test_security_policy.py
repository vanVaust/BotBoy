import unittest

from botboy.security_policy import DEFAULT_CAPABILITY_POLICY


class CapabilityPolicyTests(unittest.TestCase):
    def test_user_policy_is_minimal(self):
        self.assertEqual(DEFAULT_CAPABILITY_POLICY.for_roles(frozenset({"user"})), frozenset({"task.read", "task.resume"}))

    def test_admin_policy_contains_worker_controls(self):
        caps = DEFAULT_CAPABILITY_POLICY.for_roles(frozenset({"admin"}))
        self.assertIn("worker.register", caps)
        self.assertIn("worker.lease", caps)


if __name__ == "__main__":
    unittest.main()
