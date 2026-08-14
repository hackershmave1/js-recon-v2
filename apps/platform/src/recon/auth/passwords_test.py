"""Pure tests for bcrypt password hashing (no infra — fast lane)."""

from __future__ import annotations

from recon.auth.passwords import hash_password, verify_password


def test_hash_then_verify() -> None:
    hashed = hash_password("admin")
    assert hashed != "admin"  # never stored in plaintext
    assert verify_password("admin", hashed) is True


def test_wrong_password_rejected() -> None:
    assert verify_password("nope", hash_password("admin")) is False


def test_salted_hashes_differ() -> None:
    # Distinct salts => the same password hashes to different strings.
    assert hash_password("admin") != hash_password("admin")


def test_missing_hash_rejected() -> None:
    assert verify_password("admin", None) is False
    assert verify_password("admin", "") is False


def test_malformed_hash_never_raises() -> None:
    assert verify_password("admin", "not-a-bcrypt-hash") is False
