from __future__ import annotations

import unittest

from botboy.gateway.simple_auth_handlers import (
    handle_auth_issue_api_key,
    handle_auth_login,
    handle_auth_refresh,
    handle_principals_delete,
    handle_principals_get,
    handle_principals_post,
)


class _FakePair:
    access_token = "access"
    refresh_token = "refresh"
    expires_in = 3600
    token_type = "Bearer"


class _FakeTokenInfo:
    principal_id = "alice"
    principal_type = "user"
    roles = ["admin"]
    credential_source = "jwt"


class _FakeAuth:
    def create_pair(self, principal):
        return _FakePair()

    def refresh(self, refresh_token: str):
        return _FakePair() if refresh_token == "refresh" else None

    def verify(self, access_token: str):
        return _FakeTokenInfo()


class _FakeCredential:
    principal_id = "svc"
    role = "admin"
    label = "service"
    credential_type = "api_key"
    full_key = "full-key"
    key_id = "key-1"
    created_at = "2026-04-03T00:00:00Z"
    expires_at = "2026-04-04T00:00:00Z"


class _FakeSecretStore:
    def validate(self, api_key: str):
        if api_key == "secret":
            return _FakeCredential()
        return None

    def issue_principal_secret(self, **kwargs):
        return _FakeCredential()


class _FakePrincipal:
    def __init__(self, username: str, role: str = "admin", disabled: bool = False) -> None:
        self.username = username
        self.role = role
        self.principal_id = username
        self.disabled = disabled


class _FakePrincipalStore:
    def __init__(self) -> None:
        self.principals = {"alice": _FakePrincipal("alice")}

    def authenticate(self, username: str, password: str):
        if username == "alice" and password == "pw":
            return self.principals["alice"]
        return None

    def has_principals(self) -> bool:
        return bool(self.principals)

    def list_principals(self, include_disabled: bool = False):
        return list(self.principals.values())

    def stats(self) -> dict:
        return {"total": len(self.principals)}

    def upsert_principal(self, username: str, password: str, role: str):
        self.principals[username] = _FakePrincipal(username, role)
        return self.principals[username]

    def get_principal(self, username: str):
        return self.principals.get(username)

    def disable_principal(self, username: str):
        if username in self.principals:
            self.principals[username].disabled = True


class _FakeHandler:
    def __init__(self) -> None:
        self.botboy = object()
        self.auth_enabled = False
        self.auth = _FakeAuth()
        self.secret_store = _FakeSecretStore()
        self.principal_store = _FakePrincipalStore()
        self.json_payloads: list[tuple[dict, int]] = []

    def _json(self, payload: dict, status: int = 200) -> None:
        self.json_payloads.append((payload, status))

    def _ensure_access(self, require_auth: bool = False):
        return "tester"

    def _require_admin(self) -> bool:
        return True

    def _get_api_key_store(self):
        return self.secret_store

    def _bootstrap_principal_store(self):
        return self.principal_store

    def _principal_payload(self, principal):
        return {"username": principal.username, "role": principal.role, "disabled": principal.disabled}


class SimpleAuthHandlersTest(unittest.TestCase):
    def test_handle_auth_login_and_refresh(self) -> None:
        handler = _FakeHandler()
        handle_auth_login(handler, {"username": "alice", "password": "pw"})
        login_payload, login_status = handler.json_payloads[-1]
        handle_auth_refresh(handler, {"refresh_token": "refresh"})
        refresh_payload, refresh_status = handler.json_payloads[-1]
        self.assertEqual(login_status, 200)
        self.assertEqual(login_payload["principal"]["principal_id"], "alice")
        self.assertEqual(refresh_status, 200)
        self.assertEqual(refresh_payload["principal"]["principal_id"], "alice")

    def test_handle_auth_login_rejects_invalid_credentials_without_development_fallback(self) -> None:
        handler = _FakeHandler()
        handle_auth_login(handler, {"username": "mallory", "password": "wrong"})
        payload, status = handler.json_payloads[-1]
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"], "Invalid credentials")

    def test_handle_auth_issue_api_key(self) -> None:
        handler = _FakeHandler()
        handle_auth_issue_api_key(handler, {"principal_id": "svc", "label": "service"})
        payload, status = handler.json_payloads[-1]
        self.assertEqual(status, 200)
        self.assertEqual(payload["key_id"], "key-1")

    def test_handle_principals_crud_views(self) -> None:
        handler = _FakeHandler()
        handle_principals_get(handler, {"include_disabled": ["true"]})
        list_payload, list_status = handler.json_payloads[-1]
        handle_principals_post(handler, {"username": "bob", "password": "pw", "role": "user"})
        post_payload, post_status = handler.json_payloads[-1]
        handle_principals_delete(handler, "bob")
        delete_payload, delete_status = handler.json_payloads[-1]
        self.assertEqual(list_status, 200)
        self.assertEqual(list_payload["total"], 1)
        self.assertEqual(post_status, 200)
        self.assertEqual(post_payload["principal"]["username"], "bob")
        self.assertEqual(delete_status, 200)
        self.assertTrue(delete_payload["principal"]["disabled"])


if __name__ == "__main__":
    unittest.main()
