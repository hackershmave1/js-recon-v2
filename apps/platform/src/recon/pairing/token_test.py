"""Hermetic tests for the stateless signed pairing token (fast lane)."""

from __future__ import annotations

import pytest

from recon.pairing import token as pairing_token

_KEY = "s3rver-pairing-key-not-a-real-secret"
_TID = "f14c450a-104c-4f99-8ae6-c15c88d98a93"


def test_mint_then_verify_round_trips_the_tenant() -> None:
    tok = pairing_token.mint(_TID, key=_KEY, ttl_seconds=3600, now=1000.0)
    assert pairing_token.verify(tok, key=_KEY, now=1000.0) == _TID


def test_verify_fails_after_expiry() -> None:
    tok = pairing_token.mint(_TID, key=_KEY, ttl_seconds=100, now=1000.0)
    assert pairing_token.verify(tok, key=_KEY, now=1099.0) == _TID  # still valid
    assert pairing_token.verify(tok, key=_KEY, now=1100.0) is None  # exp reached
    assert pairing_token.verify(tok, key=_KEY, now=5000.0) is None


def test_verify_rejects_a_different_key() -> None:
    tok = pairing_token.mint(_TID, key=_KEY, ttl_seconds=3600, now=1000.0)
    assert pairing_token.verify(tok, key="another-key", now=1000.0) is None


def test_verify_rejects_a_tampered_payload() -> None:
    tok = pairing_token.mint(_TID, key=_KEY, ttl_seconds=3600, now=1000.0)
    payload_b64, sig_b64 = tok.split(".")
    # Flip the last payload char (still valid b64url) — the signature no longer matches.
    forged = payload_b64[:-1] + ("A" if payload_b64[-1] != "A" else "B")
    assert pairing_token.verify(f"{forged}.{sig_b64}", key=_KEY, now=1000.0) is None


def test_verify_rejects_a_tampered_signature() -> None:
    tok = pairing_token.mint(_TID, key=_KEY, ttl_seconds=3600, now=1000.0)
    payload_b64, sig_b64 = tok.split(".")
    forged_sig = sig_b64[:-1] + ("A" if sig_b64[-1] != "A" else "B")
    assert pairing_token.verify(f"{payload_b64}.{forged_sig}", key=_KEY, now=1000.0) is None


@pytest.mark.parametrize("bad", ["", "notatoken", "a.b.c", "onlyonepart", ".", "a."])
def test_verify_fails_closed_on_garbage(bad: str) -> None:
    assert pairing_token.verify(bad, key=_KEY, now=1000.0) is None


def test_verify_requires_a_key() -> None:
    tok = pairing_token.mint(_TID, key=_KEY, ttl_seconds=3600, now=1000.0)
    assert pairing_token.verify(tok, key="", now=1000.0) is None


def test_distinct_tenants_get_distinct_tokens() -> None:
    other = "00000000-0000-4000-8000-000000000000"
    a = pairing_token.mint(_TID, key=_KEY, ttl_seconds=3600, now=1000.0)
    b = pairing_token.mint(other, key=_KEY, ttl_seconds=3600, now=1000.0)
    assert a != b
    assert pairing_token.verify(a, key=_KEY, now=1000.0) == _TID
    assert pairing_token.verify(b, key=_KEY, now=1000.0) == other
