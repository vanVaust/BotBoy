from __future__ import annotations

import unittest

from botboy.security.zero_trust import CryptographicSignature, ZeroTrustProtocol


class ZeroTrustTest(unittest.TestCase):
    def test_zero_trust_sign_and_verify(self) -> None:
        protocol = ZeroTrustProtocol(b"a" * 32)
        payload = {"task_id": "123", "input": "test"}

        sig = protocol.sign_payload(payload)

        self.assertIsInstance(sig, CryptographicSignature)
        self.assertEqual(sig.algorithm, "HMAC-SHA256")
        self.assertNotEqual(sig.sig_hex, "")
        self.assertTrue(protocol.verify_payload(payload, sig))

    def test_zero_trust_verification_failure(self) -> None:
        protocol = ZeroTrustProtocol(b"a" * 32)
        sig = protocol.sign_payload({"task_id": "123", "input": "test"})

        self.assertFalse(protocol.verify_payload({"task_id": "123", "input": "tampered"}, sig))

    def test_zero_trust_wrap_and_unwrap(self) -> None:
        protocol = ZeroTrustProtocol(b"a" * 32)
        original_payload = {"task_id": "123", "input": "test"}

        wrapped = protocol.wrap_delegation(original_payload)
        self.assertIn("_ztrust", wrapped)
        self.assertEqual(wrapped["task_id"], "123")

        unwrapped = protocol.unwrap_and_verify(wrapped)
        self.assertNotIn("_ztrust", unwrapped)
        self.assertEqual(unwrapped["task_id"], "123")
        self.assertIn("_ztrust", wrapped)

    def test_zero_trust_missing_signature(self) -> None:
        protocol = ZeroTrustProtocol(b"a" * 32)

        with self.assertRaisesRegex(PermissionError, "Missing cryptographic signature"):
            protocol.unwrap_and_verify({"task_id": "123", "input": "test"})

    def test_zero_trust_signature_mismatch(self) -> None:
        protocol = ZeroTrustProtocol(b"a" * 32)
        wrapped = protocol.wrap_delegation({"task_id": "123", "input": "test"})
        wrapped["_ztrust"]["sig"] = "incorrect_hash"

        with self.assertRaisesRegex(PermissionError, "signature mismatch"):
            protocol.unwrap_and_verify(wrapped)


if __name__ == "__main__":
    unittest.main()
