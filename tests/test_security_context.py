import unittest

from botboy.cache import ResponseCache
from botboy.security_context import SecurityContext


class SecurityContextTests(unittest.TestCase):
    def test_context_is_immutable_and_child_can_only_reduce_capabilities(self):
        parent = SecurityContext(
            principal_id="alice",
            org_id="org-a",
            capabilities=frozenset({"read", "write"}),
            approval_scope=frozenset({"read", "write"}),
        )
        child = parent.restrict_capabilities({"read"})

        self.assertEqual(parent.capabilities, frozenset({"read", "write"}))
        self.assertEqual(child.capabilities, frozenset({"read"}))
        self.assertEqual(child.approval_scope, frozenset({"read"}))

    def test_cache_does_not_cross_security_scopes(self):
        cache = ResponseCache()
        cache.set("status", {"owner": "alice"}, security_scope="org=org-a|principal=alice")

        self.assertEqual(
            cache.get("status", security_scope="org=org-a|principal=alice"),
            {"owner": "alice"},
        )
        self.assertIsNone(
            cache.get("status", security_scope="org=org-a|principal=bob")
        )
        self.assertIsNone(
            cache.get("status", security_scope="org=org-b|principal=alice")
        )

    def test_legacy_cache_api_remains_usable(self):
        cache = ResponseCache()
        cache.set("calculate 2+2", {"value": 4})
        self.assertEqual(cache.get("calculate 2+2"), {"value": 4})


if __name__ == "__main__":
    unittest.main()
