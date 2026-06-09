"""Zero-Trust Delegation — Cryptographic verification of Agent inputs/outputs (V7)."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

@dataclass
class CryptographicSignature:
    algorithm: str
    sig_hex: str
    timestamp: str
    nonce: str

class ZeroTrustProtocol:
    """Verifies that inter-agent task payloads are not tampered with across the Enterprise Fabric."""
    
    def __init__(self, fabric_secret: bytes):
        if not fabric_secret or len(fabric_secret) < 32:
            logger.warning("[SECURITY] V7 Zero-Trust protocol initializing with insecure / short key!")
        self.secret = fabric_secret

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
        
    def _serialize_payload(self, payload: Dict[str, Any]) -> bytes:
        # Sort keys to ensure deterministic serialization
        return json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')

    def sign_payload(self, payload: Dict[str, Any]) -> CryptographicSignature:
        """Generates a cryptographic signature for a given dictionary payload."""
        import secrets
        nonce = secrets.token_hex(16)
        timestamp = self._now()
        
        data_bytes = self._serialize_payload(payload)
        # We sign: payload + nonce + timestamp
        msg = data_bytes + nonce.encode('utf-8') + timestamp.encode('utf-8')
        
        sig = hmac.new(self.secret, msg, hashlib.sha256).hexdigest()
        
        return CryptographicSignature(
            algorithm="HMAC-SHA256",
            sig_hex=sig,
            timestamp=timestamp,
            nonce=nonce
        )

    def verify_payload(self, payload: Dict[str, Any], signature: CryptographicSignature) -> bool:
        """Verifies if the payload exactly matches the cryptographic signature."""
        data_bytes = self._serialize_payload(payload)
        msg = data_bytes + signature.nonce.encode('utf-8') + signature.timestamp.encode('utf-8')
        
        expected_sig = hmac.new(self.secret, msg, hashlib.sha256).hexdigest()
        
        # Constant-time comparison
        return hmac.compare_digest(expected_sig, signature.sig_hex)

    def wrap_delegation(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Wraps a task payload with `_ztrust` envelope for cross-node transport."""
        sig = self.sign_payload(payload)
        wrapped = dict(payload)
        wrapped["_ztrust"] = {
            "algo": sig.algorithm,
            "sig": sig.sig_hex,
            "ts": sig.timestamp,
            "nonce": sig.nonce
        }
        return wrapped

    def unwrap_and_verify(self, wrapped_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Unwraps a delegated payload and verifies its integrity. Raises ValueError if tampered."""
        if "_ztrust" not in wrapped_payload:
            raise PermissionError("Zero-Trust Delegation failed: Missing cryptographic signature.")

        # Copy to avoid mutating the caller's dict
        payload_copy = dict(wrapped_payload)
        ztrust = payload_copy.pop("_ztrust")
        sig = CryptographicSignature(
            algorithm=ztrust["algo"],
            sig_hex=ztrust["sig"],
            timestamp=ztrust["ts"],
            nonce=ztrust["nonce"]
        )

        if not self.verify_payload(payload_copy, sig):
            raise PermissionError("Zero-Trust Delegation failed: Cryptographic signature mismatch! Payload tampered.")

        return payload_copy
