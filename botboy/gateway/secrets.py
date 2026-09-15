"""SecretStore - principal credential management with PBKDF2-HMAC-SHA256."""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from botboy.db_mixin import _SQLiteMixin


@dataclass
class ApiKey:
    key_id: str
    role: str
    label: str
    created_at: str
    expires_at: Optional[str]
    use_count: int
    revoked: bool
    org_id: str = "default"
    full_key: Optional[str] = None

    @property
    def principal_id(self) -> str:
        return self.key_id

    @property
    def credential_type(self) -> str:
        return "api_key"


@dataclass
class Principal:
    username: str
    role: str
    created_at: str
    updated_at: str
    disabled: bool = False
    org_id: str = "default"

    @property
    def principal_id(self) -> str:
        return self.username

    @property
    def credential_type(self) -> str:
        return "password"


_PBKDF2_ITERATIONS = 260_000
_PBKDF2_ALGO = "sha256"


def _hash_key(secret: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac(_PBKDF2_ALGO, secret.encode(), salt, _PBKDF2_ITERATIONS)
    return salt.hex() + "$" + dk.hex()


def _verify_key(secret: str, stored: str) -> bool:
    if not isinstance(secret, str) or not isinstance(stored, str):
        return False
    salt_hex, separator, dk_hex = stored.partition("$")
    if separator != "$" or not salt_hex or not dk_hex:
        return False
    try:
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac(_PBKDF2_ALGO, secret.encode(), salt, _PBKDF2_ITERATIONS)
    return secrets.compare_digest(dk.hex(), dk_hex)


def _parse_store_timestamp(value: str) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _normalize_principal_id(principal_id: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9._-]+", "-", principal_id.strip()).strip(".-_")
    if not value:
        raise ValueError("principal_id cannot be empty")
    return value


_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_keys (
    key_id TEXT PRIMARY KEY,
    key_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    label TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    expires_at TEXT,
    use_count INTEGER NOT NULL DEFAULT 0,
    revoked INTEGER NOT NULL DEFAULT 0,
    org_id TEXT NOT NULL DEFAULT 'default'
);
CREATE INDEX IF NOT EXISTS idx_api_keys_revoked ON api_keys(revoked);
CREATE INDEX IF NOT EXISTS idx_api_keys_org_id ON api_keys(org_id);
"""

_PRINCIPAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS principals (
    username TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    disabled INTEGER NOT NULL DEFAULT 0,
    org_id TEXT NOT NULL DEFAULT 'default'
);
CREATE INDEX IF NOT EXISTS idx_principals_disabled ON principals(disabled);
CREATE INDEX IF NOT EXISTS idx_principals_org_id ON principals(org_id);
"""


class SecretStore(_SQLiteMixin):
    """Persistent API-key store; cleartext keys are never stored."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self._init_connection_pool(db_path, _SCHEMA)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def generate(self, role: str = "user", label: str = "", ttl_hours: Optional[float] = None,
                 principal_id: Optional[str] = None, org_id: str = "default") -> ApiKey:
        key_id = _normalize_principal_id(principal_id) if principal_id else secrets.token_hex(8)
        secret = secrets.token_urlsafe(24)
        full_key = f"bbk_{key_id}_{secret}"
        key_hash = _hash_key(secret)
        now = self._now()
        expires_at = None
        if ttl_hours is not None and ttl_hours > 0:
            from datetime import timedelta
            expires_at = (datetime.now(timezone.utc) + timedelta(hours=ttl_hours)).isoformat()
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO api_keys (key_id,key_hash,role,label,created_at,expires_at,org_id) VALUES (?,?,?,?,?,?,?)",
            (key_id, key_hash, role, label, now, expires_at, org_id or "default"),
        )
        conn.commit()
        return ApiKey(key_id, role, label, now, expires_at, 0, False, org_id or "default", full_key)

    def issue_principal_secret(self, principal_id: str, role: str = "user", label: str = "",
                               ttl_hours: Optional[float] = None, org_id: str = "default") -> ApiKey:
        return self.generate(role=role, label=label, ttl_hours=ttl_hours, principal_id=principal_id, org_id=org_id)

    def validate(self, full_key: str) -> Optional[ApiKey]:
        if not isinstance(full_key, str):
            return None
        parts = full_key.split("_", 2)
        if len(parts) != 3 or parts[0] != "bbk":
            return None
        key_id, secret = parts[1], parts[2]
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM api_keys WHERE key_id = ? AND revoked = 0", (key_id,)).fetchone()
        if not row:
            return None
        if row["expires_at"]:
            exp = _parse_store_timestamp(row["expires_at"])
            if exp is None or datetime.now(timezone.utc) > exp:
                return None
        if not _verify_key(secret, row["key_hash"]):
            return None
        conn.execute("UPDATE api_keys SET use_count = use_count + 1 WHERE key_id = ?", (key_id,))
        conn.commit()
        return ApiKey(key_id=row["key_id"], role=row["role"], label=row["label"],
                      created_at=row["created_at"], expires_at=row["expires_at"],
                      use_count=row["use_count"] + 1, revoked=False, org_id=row["org_id"] or "default")

    def revoke(self, key_id: str) -> bool:
        conn = self._get_conn()
        cur = conn.execute("UPDATE api_keys SET revoked = 1 WHERE key_id = ? AND revoked = 0", (key_id,))
        conn.commit()
        return cur.rowcount == 1

    def rotate(self, key_id: str, label: str = "") -> Optional[ApiKey]:
        """Atomically revoke an active key and create its replacement, preserving role and tenant."""
        conn = self._get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT role, label, expires_at, org_id FROM api_keys WHERE key_id = ? AND revoked = 0",
                (key_id,),
            ).fetchone()
            if not row:
                conn.rollback()
                return None
            ttl = None
            if row["expires_at"]:
                exp = _parse_store_timestamp(row["expires_at"])
                if exp is None:
                    conn.rollback()
                    return None
                remaining = (exp - datetime.now(timezone.utc)).total_seconds() / 3600
                if remaining <= 0:
                    conn.rollback()
                    return None
                ttl = remaining
            new_key_id = secrets.token_hex(8)
            secret = secrets.token_urlsafe(24)
            now = self._now()
            expires_at = None
            if ttl is not None:
                from datetime import timedelta
                expires_at = (datetime.now(timezone.utc) + timedelta(hours=ttl)).isoformat()
            conn.execute("UPDATE api_keys SET revoked = 1 WHERE key_id = ? AND revoked = 0", (key_id,))
            conn.execute(
                "INSERT INTO api_keys (key_id,key_hash,role,label,created_at,expires_at,org_id) VALUES (?,?,?,?,?,?,?)",
                (new_key_id, _hash_key(secret), row["role"], label or f"rotated-{key_id[:6]}", now, expires_at, row["org_id"] or "default"),
            )
            conn.commit()
            return ApiKey(new_key_id, row["role"], label or f"rotated-{key_id[:6]}", now, expires_at,
                          0, False, row["org_id"] or "default", f"bbk_{new_key_id}_{secret}")
        except Exception:
            conn.rollback()
            raise

    def list_keys(self, role: Optional[str] = None, include_revoked: bool = False) -> List[ApiKey]:
        conn = self._get_conn()
        conditions, params = [], []
        if not include_revoked:
            conditions.append("revoked = 0")
        if role:
            conditions.append("role = ?")
            params.append(role)
        query = "SELECT * FROM api_keys" + ((" WHERE " + " AND ".join(conditions)) if conditions else "") + " ORDER BY created_at DESC"
        rows = conn.execute(query, params).fetchall()
        return [ApiKey(r["key_id"], r["role"], r["label"], r["created_at"], r["expires_at"],
                        r["use_count"], bool(r["revoked"]), r["org_id"] or "default") for r in rows]

    def stats(self) -> dict:
        row = self._get_conn().execute("SELECT COUNT(*) total, SUM(revoked) revoked_count, SUM(use_count) total_uses FROM api_keys").fetchone()
        return {"total": row["total"], "active": row["total"] - (row["revoked_count"] or 0),
                "revoked": row["revoked_count"] or 0, "total_uses": row["total_uses"] or 0}

    def purge_expired(self) -> int:
        conn = self._get_conn()
        cur = conn.execute("DELETE FROM api_keys WHERE expires_at IS NOT NULL AND expires_at < ?", (self._now(),))
        conn.commit()
        return cur.rowcount

    def close(self) -> None:
        super().close()


class PrincipalStore(_SQLiteMixin):
    """Persistent username/password credential store for interactive principals."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self._init_connection_pool(db_path, _PRINCIPAL_SCHEMA)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _normalize_username(username: str) -> str:
        return _normalize_principal_id(username).lower()

    @staticmethod
    def _row_to_principal(row) -> Principal:
        try:
            org_id = row["org_id"]
        except (IndexError, KeyError, TypeError):
            org_id = "default"
        return Principal(row["username"], row["role"], row["created_at"], row["updated_at"], bool(row["disabled"]), org_id or "default")

    def upsert_principal(self, username: str, password: str, role: str = "user", org_id: str = "default") -> Principal:
        username = self._normalize_username(username)
        if not password:
            raise ValueError("password is required")
        now = self._now()
        conn = self._get_conn()
        existing = conn.execute("SELECT created_at FROM principals WHERE username = ?", (username,)).fetchone()
        created_at = existing["created_at"] if existing else now
        conn.execute("""
            INSERT INTO principals (username,password_hash,role,created_at,updated_at,disabled,org_id)
            VALUES (?,?,?,?,?,0,?)
            ON CONFLICT(username) DO UPDATE SET password_hash=excluded.password_hash,
                role=excluded.role, updated_at=excluded.updated_at, disabled=0, org_id=excluded.org_id
        """, (username, _hash_key(password), role, created_at, now, org_id or "default"))
        conn.commit()
        return self._row_to_principal(conn.execute("SELECT * FROM principals WHERE username = ?", (username,)).fetchone())

    def authenticate(self, username: str, password: str) -> Optional[Principal]:
        username = self._normalize_username(username)
        row = self._get_conn().execute("SELECT * FROM principals WHERE username = ? AND disabled = 0", (username,)).fetchone()
        if not row or not _verify_key(password, row["password_hash"]):
            return None
        return self._row_to_principal(row)

    def get_principal(self, username: str) -> Optional[Principal]:
        username = self._normalize_username(username)
        row = self._get_conn().execute("SELECT * FROM principals WHERE username = ?", (username,)).fetchone()
        return self._row_to_principal(row) if row else None

    def list_principals(self, include_disabled: bool = False) -> List[Principal]:
        query = "SELECT * FROM principals" + (" WHERE disabled = 0" if not include_disabled else "") + " ORDER BY username"
        return [self._row_to_principal(row) for row in self._get_conn().execute(query).fetchall()]

    def has_principals(self) -> bool:
        return bool(self._get_conn().execute("SELECT COUNT(*) total FROM principals").fetchone()["total"])

    def disable_principal(self, username: str) -> bool:
        username = self._normalize_username(username)
        conn = self._get_conn()
        conn.execute("UPDATE principals SET disabled = 1, updated_at = ? WHERE username = ?", (self._now(), username))
        conn.commit()
        return True

    def stats(self) -> dict:
        row = self._get_conn().execute("SELECT COUNT(*) total, SUM(disabled) disabled_count FROM principals").fetchone()
        return {"total": row["total"], "active": row["total"] - (row["disabled_count"] or 0), "disabled": row["disabled_count"] or 0}


_TOKEN_REVOCATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS revoked_tokens (
    jti TEXT PRIMARY KEY,
    revoked_at TEXT NOT NULL,
    expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_revoked_tokens_expires_at ON revoked_tokens(expires_at);
"""


class TokenRevocationStore(_SQLiteMixin):
    def __init__(self, db_path: str = ":memory:") -> None:
        self._init_connection_pool(db_path, _TOKEN_REVOCATION_SCHEMA)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def revoke(self, jti: str, exp: int) -> None:
        if not jti:
            return
        conn = self._get_conn()
        conn.execute("INSERT OR IGNORE INTO revoked_tokens (jti,revoked_at,expires_at) VALUES (?,?,?)", (jti, self._now(), exp))
        conn.commit()

    def is_revoked(self, jti: str) -> bool:
        if not jti:
            return False
        return bool(self._get_conn().execute("SELECT 1 FROM revoked_tokens WHERE jti = ?", (jti,)).fetchone())

    def purge_expired(self) -> int:
        conn = self._get_conn()
        cur = conn.execute("DELETE FROM revoked_tokens WHERE expires_at < ?", (int(time.time()),))
        conn.commit()
        return cur.rowcount
