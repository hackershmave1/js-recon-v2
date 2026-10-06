"""Stored-key encryption: every unreadable key surfaces as KeyDecryptError, never a 500."""

import pytest
from cryptography.fernet import Fernet

from recon.config import get_settings
from recon.llm.crypto import KeyDecryptError, decrypt_api_key, encrypt_api_key


@pytest.fixture()
def set_encryption_key(monkeypatch):
    def _set(value: str) -> None:
        monkeypatch.setenv("RECON_LLM_ENCRYPTION_KEY", value)
        get_settings.cache_clear()

    get_settings.cache_clear()
    yield _set
    get_settings.cache_clear()


def test_round_trip_and_ciphertext_differs(set_encryption_key):
    set_encryption_key(Fernet.generate_key().decode())
    ciphertext = encrypt_api_key("sk-secret")
    assert ciphertext != "sk-secret"
    assert decrypt_api_key(ciphertext) == "sk-secret"


def test_decrypt_with_a_different_key_fails(set_encryption_key):
    set_encryption_key(Fernet.generate_key().decode())
    ciphertext = encrypt_api_key("sk-secret")
    set_encryption_key(Fernet.generate_key().decode())
    with pytest.raises(KeyDecryptError):
        decrypt_api_key(ciphertext)


def test_cleartext_stored_before_a_key_was_set_fails(set_encryption_key):
    set_encryption_key("")
    stored = encrypt_api_key("sk-secret")
    assert stored == "sk-secret"  # dev mode stores cleartext
    set_encryption_key(Fernet.generate_key().decode())
    with pytest.raises(KeyDecryptError):
        decrypt_api_key(stored)


def test_malformed_encryption_key_fails_as_decrypt_error(set_encryption_key):
    set_encryption_key("not-a-fernet-key")
    with pytest.raises(KeyDecryptError):
        decrypt_api_key("anything")
