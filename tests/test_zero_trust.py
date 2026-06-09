import pytest
from botboy.security.zero_trust import ZeroTrustProtocol, CryptographicSignature

def test_zero_trust_sign_and_verify():
    secret = b"a" * 32
    protocol = ZeroTrustProtocol(secret)
    payload = {"task_id": "123", "input": "test"}

    sig = protocol.sign_payload(payload)
    assert isinstance(sig, CryptographicSignature)
    assert sig.algorithm == "HMAC-SHA256"
    assert sig.sig_hex != ""

    assert protocol.verify_payload(payload, sig) is True

def test_zero_trust_verification_failure():
    secret = b"a" * 32
    protocol = ZeroTrustProtocol(secret)
    payload = {"task_id": "123", "input": "test"}

    sig = protocol.sign_payload(payload)

    # Tamper payload
    tampered_payload = {"task_id": "123", "input": "tampered"}
    assert protocol.verify_payload(tampered_payload, sig) is False

def test_zero_trust_wrap_and_unwrap():
    secret = b"a" * 32
    protocol = ZeroTrustProtocol(secret)
    original_payload = {"task_id": "123", "input": "test"}

    # Wrap delegation
    wrapped = protocol.wrap_delegation(original_payload)
    assert "_ztrust" in wrapped
    assert wrapped["task_id"] == "123"

    # Unwrap and verify
    unwrapped = protocol.unwrap_and_verify(wrapped)
    assert "_ztrust" not in unwrapped
    assert unwrapped["task_id"] == "123"

    # Check original dict was not mutated
    assert "_ztrust" in wrapped

def test_zero_trust_missing_signature():
    secret = b"a" * 32
    protocol = ZeroTrustProtocol(secret)
    payload = {"task_id": "123", "input": "test"}

    with pytest.raises(PermissionError, match="Missing cryptographic signature"):
        protocol.unwrap_and_verify(payload)

def test_zero_trust_signature_mismatch():
    secret = b"a" * 32
    protocol = ZeroTrustProtocol(secret)
    payload = {"task_id": "123", "input": "test"}

    wrapped = protocol.wrap_delegation(payload)
    wrapped["_ztrust"]["sig"] = "incorrect_hash"

    with pytest.raises(PermissionError, match="signature mismatch"):
        protocol.unwrap_and_verify(wrapped)
