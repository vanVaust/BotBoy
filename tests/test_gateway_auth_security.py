from __future__ import annotations

import base64
import json
import unittest
from types import SimpleNamespace

from fastapi import Request

from botboy.gateway.routes_auth import create_auth_router
from botboy.gateway.auth import JWTAuth, _b64url_encode
from botboy.gateway.secrets import PrincipalStore, SecretStore, _verify_key


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

    def test_secret_store_validate_preserves_org_id(self) -> None:
        store = SecretStore()
        self.addCleanup(store.close)
        key = store.generate(org_id="tenant-a")
        validated = store.validate(key.full_key)
        self.assertIsNotNone(validated)
        self.assertEqual(validated.org_id, "tenant-a")

    def test_secret_store_rotate_is_atomic_and_preserves_security_scope(self) -> None:
        store = SecretStore()
        self.addCleanup(store.close)
        key = store.generate(role="worker", label="original", ttl_hours=1, org_id="tenant-a")
        rotated = store.rotate(key.key_id, label="replacement")
        self.assertIsNotNone(rotated)
        self.assertNotEqual(rotated.key_id, key.key_id)
        self.assertEqual(rotated.role, "worker")
        self.assertEqual(rotated.org_id, "tenant-a")
        self.assertIsNone(store.validate(key.full_key))
        replacement = store.validate(rotated.full_key)
        self.assertIsNotNone(replacement)
        self.assertEqual(replacement.org_id, "tenant-a")
        self.assertEqual(replacement.role, "worker")

    def test_jwt_round_trip_preserves_org_id(self) -> None:
        auth = JWTAuth("x" * 32)
        pair = auth.create_pair("alice", ["user"], org_id="tenant-a")
        info = auth.verify(pair.access_token)
        self.assertIsNotNone(info)
        self.assertEqual(info.org_id, "tenant-a")

        refreshed = auth.refresh(pair.refresh_token)
        self.assertIsNotNone(refreshed)
        refreshed_info = auth.verify(refreshed.access_token)
        self.assertIsNotNone(refreshed_info)
        self.assertEqual(refreshed_info.org_id, "tenant-a")

    def test_jwt_rejects_missing_or_invalid_org_id_claim(self) -> None:
        auth = JWTAuth("x" * 32)
        pair = auth.create_pair("alice", ["user"], org_id="tenant-a")
        header, payload, _sig = pair.access_token.split(".")
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        claims.pop("org_id", None)
        mutated_payload = _b64url_encode(json.dumps(claims).encode())
        mutated = f"{header}.{mutated_payload}.{auth._sign(header, mutated_payload)}"
        self.assertIsNone(auth.verify(mutated))

        for invalid_org_id in ("", "   ", 123, [], {}, None):
            claims["org_id"] = invalid_org_id
            mutated_payload = _b64url_encode(json.dumps(claims).encode())
            mutated = f"{header}.{mutated_payload}.{auth._sign(header, mutated_payload)}"
            self.assertIsNone(auth.verify(mutated))

    def test_jwt_refresh_rejects_missing_or_invalid_org_id_claim(self) -> None:
        auth = JWTAuth("x" * 32)
        pair = auth.create_pair("alice", ["user"], org_id="tenant-a")
        header, payload, _sig = pair.refresh_token.split(".")
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))

        for invalid_org_id in (None, "", "   ", 123, [], {}):
            mutated_claims = dict(claims)
            if invalid_org_id is None:
                mutated_claims.pop("org_id", None)
            else:
                mutated_claims["org_id"] = invalid_org_id
            mutated_payload = _b64url_encode(json.dumps(mutated_claims).encode())
            mutated = f"{header}.{mutated_payload}.{auth._sign(header, mutated_payload)}"
            self.assertIsNone(auth.refresh(mutated))


    def test_refresh_rejects_changed_principal_role_or_tenant(self) -> None:
        auth = JWTAuth("x" * 32)
        store = PrincipalStore()
        self.addCleanup(store.close)
        store.upsert_principal("alice", "password", role="user", org_id="tenant-a")
        pair = auth.create_pair("alice", ["user"], org_id="tenant-a")

        ctx = SimpleNamespace(
            auth=auth,
            auth_enabled=True,
            authorize=lambda *args, **kwargs: "alice",
            bootstrap_principal_store=lambda: store,
            get_api_key_store=lambda: None,
        )
        router = create_auth_router(ctx)
        endpoint = next(route.endpoint for route in router.routes if route.path == "/api/auth/refresh")

        async def call_refresh() -> None:
            body = json.dumps({"refresh_token": pair.refresh_token}).encode()

            async def receive():
                return {"type": "http.request", "body": body, "more_body": False}

            request = Request(
                {"type": "http", "method": "POST", "path": "/api/auth/refresh", "headers": [], "client": ("127.0.0.1", 1)},
                receive,
            )
            await endpoint(request)

        store.upsert_principal("alice", "password", role="admin", org_id="tenant-a")
        with self.assertRaises(Exception) as role_error:
            import asyncio
            asyncio.run(call_refresh())
        self.assertEqual(getattr(role_error.exception, "status_code", None), 401)

        pair = auth.create_pair("alice", ["user"], org_id="tenant-a")
        store.upsert_principal("alice", "password", role="user", org_id="tenant-b")
        with self.assertRaises(Exception) as org_error:
            import asyncio
            asyncio.run(call_refresh())
        self.assertEqual(getattr(org_error.exception, "status_code", None), 401)

    def test_jwt_revocation(self) -> None:
        auth = JWTAuth("x" * 32)
        pair = auth.create_pair("admin")
        self.assertIsNotNone(auth.verify(pair.access_token))
        self.assertTrue(auth.revoke_token(pair.access_token))
        self.assertIsNone(auth.verify(pair.access_token))
        self.assertIsNotNone(auth.refresh(pair.refresh_token))
        self.assertTrue(auth.revoke_token(pair.refresh_token))
        self.assertIsNone(auth.refresh(pair.refresh_token))
