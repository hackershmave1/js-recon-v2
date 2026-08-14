"""Pure tests for the stateless auth-session token (no infra — fast lane)."""

from __future__ import annotations

from recon.auth import token as auth_token
from recon.auth.token import _b64url_encode, _sign
from recon.pairing import token as pairing_token

KEY = "auth-secret-key"


def _mint(**over) -> str:
    base = {
        "user_id": "user-1",
        "tenant_id": "tenant-9",
        "role": "admin",
        "key": KEY,
        "ttl_seconds": 3600,
    }
    base.update(over)
    return auth_token.mint(**base)


def test_roundtrip_returns_claims() -> None:
    claims = auth_token.verify(_mint(), key=KEY)
    assert claims is not None
    assert claims.user_id == "user-1"
    assert claims.tenant_id == "tenant-9"
    assert claims.role == "admin"


def test_wrong_key_rejected() -> None:
    assert auth_token.verify(_mint(), key="different-key") is None


def test_expired_rejected() -> None:
    # Minted already-expired (negative ttl), verified at default now => past exp.
    assert auth_token.verify(_mint(ttl_seconds=-1), key=KEY) is None


def test_not_yet_expired_boundary() -> None:
    token = auth_token.mint(
        user_id="u", tenant_id="t", role="r", key=KEY, ttl_seconds=100, now=1000
    )
    assert auth_token.verify(token, key=KEY, now=1050) is not None
    assert auth_token.verify(token, key=KEY, now=1100) is None  # >= exp fails closed


def test_tampered_payload_rejected() -> None:
    payload_b64, sig_b64 = _mint().split(".")
    # Flip the payload but keep the old signature => HMAC mismatch.
    forged = _b64url_encode(
        b'{"typ":"auth","sub":"evil","t":"other","role":"admin","exp":9999999999}'
    )
    assert auth_token.verify(f"{forged}.{sig_b64}", key=KEY) is None


def test_pairing_token_rejected_even_with_same_key() -> None:
    # Domain separation: a pairing token (no "typ") must never verify as an auth
    # token, even if an operator misconfigures both secrets to the same value.
    paired = pairing_token.mint("tenant-9", key=KEY, ttl_seconds=3600)
    assert auth_token.verify(paired, key=KEY) is None


def test_wrong_typ_rejected() -> None:
    # A well-signed token carrying a non-"auth" typ is rejected.
    payload = _b64url_encode(b'{"typ":"pair","sub":"u","t":"t","role":"admin","exp":9999999999}')
    token = f"{payload}.{_sign(payload, KEY)}"
    assert auth_token.verify(token, key=KEY) is None


def test_missing_claim_rejected() -> None:
    # typ=auth but no "role": fails closed rather than defaulting a permissive role.
    payload = _b64url_encode(b'{"typ":"auth","sub":"u","t":"t","exp":9999999999}')
    token = f"{payload}.{_sign(payload, KEY)}"
    assert auth_token.verify(token, key=KEY) is None


def test_empty_and_malformed_rejected() -> None:
    assert auth_token.verify("", key=KEY) is None
    assert auth_token.verify("not-a-token", key=KEY) is None
    assert auth_token.verify(_mint(), key="") is None  # empty key never verifies
    assert auth_token.verify("\x80.\x80", key=KEY) is None  # non-ascii never raises
