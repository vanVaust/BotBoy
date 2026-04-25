from __future__ import annotations

import base64
import json
import time
import unittest

from botboy.gateway.auth import JWTAuth, _b64url_encode
from botboy.gateway.secrets import SecretStore, _verify_key


class GatewayAuthSecurityTest(unittest.TestCase):
    def test_jwt_verify_returns_none_for_invalid_base64_payload(self) -> None:
        auth = JWTAuth("x" * 32)
        token = "header.invalid***.sig"

        self.assertIsNone(auth.verify(token))

    def test_jwt_verify_returns_none_for_invalid_json_payload(self) -> None:
        auth = JWTAuth("x" * 32)
        header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        payload = _b64url_encode(base64.urlsafe_b64encode(b"not-json"))
        token = f"{header}.{payload}.{auth._sign(header, payload)}"

        self.assertIsNone(auth.verify(token))

    def test_jwt_verify_returns_none_for_non_object_json_payload(self) -> None:
        auth = JWTAuth("x" * 32)
        header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        payload = _b64url_encode(json.dumps(["not", "an", "object"]).encode())
        token = f"{header}.{payload}.{auth._sign(header, payload)}"

        self.assertIsNone(auth.verify(token))

    def test_jwt_verify_returns_none_when_subject_missing(self) -> None:
        auth = JWTAuth("x" * 32)
        header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        payload = _b64url_encode(
            json.dumps(
                {
                    "roles": ["admin"],
                    "iat": int(time.time()),
                    "exp": int(time.time()) + 3600,
                    "jti": "tid",
                    "type": "access",
                }
            ).encode()
        )
        token = f"{header}.{payload}.{auth._sign(header, payload)}"

        self.assertIsNone(auth.verify(token))

    def test_jwt_refresh_returns_none_when_subject_missing(self) -> None:
        auth = JWTAuth("x" * 32)
        header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        payload = _b64url_encode(
            json.dumps(
                {
                    "roles": ["admin"],
                    "iat": int(time.time()),
                    "exp": int(time.time()) + 3600,
                    "jti": "tid",
                    "type": "refresh",
                }
            ).encode()
        )
        token = f"{header}.{payload}.{auth._sign(header, payload)}"

        self.assertIsNone(auth.refresh(token))

    def test_verify_key_returns_false_for_malformed_hash_material(self) -> None:
        self.assertFalse(_verify_key("secret", "not-a-valid-hash"))
        self.assertFalse(_verify_key("secret", "zzzz$abcd"))
        self.assertFalse(_verify_key("secret", None))  # type: ignore[arg-type]

    def test_secret_store_validate_returns_none_for_non_string_key(self) -> None:
        store = SecretStore()
        self.addCleanup(store.close)

        self.assertIsNone(store.validate(None))  # type: ignore[arg-type]
        self.assertIsNone(store.validate("bbk_only_two_parts"))

    def test_secret_store_validate_returns_none_for_malformed_expiry(self) -> None:
        store = SecretStore()
        self.addCleanup(store.close)
        key = store.generate()
        conn = store._get_conn()
        conn.execute("UPDATE api_keys SET expires_at = ? WHERE key_id = ?", ("not-a-timestamp", key.key_id))
        conn.commit()

        self.assertIsNone(store.validate(key.full_key))

    def test_secret_store_rotate_returns_none_for_malformed_expiry(self) -> None:
        store = SecretStore()
        self.addCleanup(store.close)
        key = store.generate(ttl_hours=1)
        conn = store._get_conn()
        conn.execute("UPDATE api_keys SET expires_at = ? WHERE key_id = ?", ("not-a-timestamp", key.key_id))
        conn.commit()

        self.assertIsNone(store.rotate(key.key_id))
