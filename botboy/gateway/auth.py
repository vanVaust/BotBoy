"""JWT authentication and principal modeling using stdlib-only HS256."""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(frozen=True)
class AuthPrincipal:
    """A normalized authenticated principal."""

    principal_id: str
    roles: List[str]
    principal_type: str = "user"
    credential_source: str = "bootstrap"
    display_name: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int
    token_type: str = "Bearer"


@dataclass(frozen=True)
class TokenInfo:
    principal_id: str
    roles: List[str]
    issued_at: int
    expires_at: int
    token_id: str
    principal_type: str = "user"
    credential_source: str = "jwt"

    @property
    def user_id(self) -> str:
        """Backward-compatible alias for the principal identifier."""
        return self.principal_id


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    if not isinstance(s, str):
        raise TypeError("base64 input must be a string")
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


class JWTAuth:
    """
    HS256 JWT authentication using stdlib only.

    Token structure:
        header.payload.signature  (standard JWT)
    """

    ACCESS_TTL = 3600        # 1 hour
    REFRESH_TTL = 604800     # 7 days
    MIN_SECRET_LEN = 32

    def __init__(self, secret: str = "", *, allow_generate: bool = False, revocation_store: Any = None) -> None:
        if not secret:
            secret = os.getenv("BOTBOY_JWT_SECRET", "")
        if not secret and allow_generate:
            secret = secrets.token_hex(32)
        if not secret:
            raise ValueError("JWT secret must be configured")
        if len(secret) < self.MIN_SECRET_LEN:
            raise ValueError(f"JWT secret must be at least {self.MIN_SECRET_LEN} characters")
        self._secret = secret.encode()
        self._revocation_store = revocation_store
        self._local_revoked_jtis = set()

    @staticmethod
    def _coerce_principal(
        principal: str | AuthPrincipal,
        roles: Optional[List[str]] = None,
        principal_type: Optional[str] = None,
        credential_source: str = "bootstrap",
    ) -> AuthPrincipal:
        if isinstance(principal, AuthPrincipal):
            merged_roles = list(principal.roles or roles or ["user"])
            return AuthPrincipal(
                principal_id=principal.principal_id,
                roles=merged_roles,
                principal_type=principal.principal_type,
                credential_source=principal.credential_source,
                display_name=principal.display_name,
                metadata=dict(principal.metadata or {}),
            )
        return AuthPrincipal(
            principal_id=str(principal),
            roles=list(roles or ["user"]),
            principal_type=principal_type or "user",
            credential_source=credential_source,
        )

    def _sign(self, header_b64: str, payload_b64: str) -> str:
        msg = f"{header_b64}.{payload_b64}".encode()
        sig = hmac.new(self._secret, msg, hashlib.sha256).digest()
        return _b64url_encode(sig)

    def _encode(self, payload: dict) -> str:
        header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        body = _b64url_encode(json.dumps(payload).encode())
        sig = self._sign(header, body)
        return f"{header}.{body}.{sig}"

    def _decode(self, token: str) -> Optional[dict]:
        try:
            if not isinstance(token, str):
                return None
            parts = token.split(".")
            if len(parts) != 3:
                return None
            header_b64, body_b64, sig = parts
            expected_sig = self._sign(header_b64, body_b64)
            if not hmac.compare_digest(expected_sig, sig):
                return None
            payload = json.loads(_b64url_decode(body_b64))
            if not isinstance(payload, dict):
                return None
            return payload
        except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error):
            return None

    def create_pair(
        self,
        principal: str | AuthPrincipal,
        roles: List[str] = None,
        *,
        principal_type: Optional[str] = None,
        credential_source: str = "bootstrap",
    ) -> TokenPair:
        now = int(time.time())
        tid = secrets.token_hex(8)
        principal_obj = self._coerce_principal(
            principal,
            roles=roles,
            principal_type=principal_type,
            credential_source=credential_source,
        )

        access_payload = {
            "sub": principal_obj.principal_id,
            "roles": principal_obj.roles,
            "iat": now,
            "exp": now + self.ACCESS_TTL,
            "jti": tid,
            "type": "access",
            "ptype": principal_obj.principal_type,
            "src": principal_obj.credential_source,
            "name": principal_obj.display_name,
        }
        refresh_payload = {
            "sub": principal_obj.principal_id,
            "roles": principal_obj.roles,
            "iat": now,
            "exp": now + self.REFRESH_TTL,
            "jti": secrets.token_hex(8),
            "type": "refresh",
            "ptype": principal_obj.principal_type,
            "src": principal_obj.credential_source,
            "name": principal_obj.display_name,
        }
        return TokenPair(
            access_token=self._encode(access_payload),
            refresh_token=self._encode(refresh_payload),
            expires_in=self.ACCESS_TTL,
        )

    @staticmethod
    def extract_bearer_token(header_value: str = "") -> str:
        """Extract a Bearer token from an Authorization header."""
        if not header_value:
            return ""
        if header_value.lower().startswith("bearer "):
            return header_value[7:].strip()
        return ""

    def is_revoked(self, jti: str) -> bool:
        if self._revocation_store and hasattr(self._revocation_store, "is_revoked"):
            return self._revocation_store.is_revoked(jti)
        return jti in self._local_revoked_jtis

    def revoke_jti(self, jti: str, exp: int = 0) -> None:
        if self._revocation_store and hasattr(self._revocation_store, "revoke"):
            self._revocation_store.revoke(jti, exp)
        else:
            self._local_revoked_jtis.add(jti)

    def revoke_token(self, token: str) -> bool:
        """Revokes a specific token."""
        payload = self._decode(token)
        if not payload:
            return False
        jti = payload.get("jti")
        if not jti:
            return False
        self.revoke_jti(jti, payload.get("exp", 0))
        return True

    def verify(self, token: str, expected_type: str = "access") -> Optional[TokenInfo]:
        payload = self._decode(token)
        if not payload:
            return None
        now = int(time.time())
        if payload.get("exp", 0) < now:
            return None
        if payload.get("type") != expected_type:
            return None
        jti = payload.get("jti", "")
        if jti and self.is_revoked(jti):
            return None
        return TokenInfo(
            principal_id=payload["sub"],
            roles=payload.get("roles", []),
            issued_at=payload.get("iat", 0),
            expires_at=payload.get("exp", 0),
            token_id=jti,
            principal_type=payload.get("ptype", "user"),
            credential_source=payload.get("src", "jwt"),
        )

    def refresh(self, refresh_token: str) -> Optional[TokenPair]:
        payload = self._decode(refresh_token)
        if not payload:
            return None
        now = int(time.time())
        if payload.get("exp", 0) < now:
            return None
        if payload.get("type") != "refresh":
            return None
        jti = payload.get("jti", "")
        if jti and self.is_revoked(jti):
            return None
        
        # Optionally, revoke the used refresh token (refresh token rotation)
        self.revoke_jti(jti, payload.get("exp", 0))

        return self.create_pair(
            payload["sub"],
            payload.get("roles", ["user"]),
            principal_type=payload.get("ptype", "user"),
            credential_source=payload.get("src", "jwt"),
        )

