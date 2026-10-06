"""API-key encryption for stored LLM keys (session and team).

Fernet with ``RECON_LLM_ENCRYPTION_KEY``. Empty key (dev default) stores the key in
cleartext; operators MUST set it in any real deployment."""

from __future__ import annotations

from recon.config import get_settings


class KeyDecryptError(Exception):
    """A stored key can't be decrypted (usually RECON_LLM_ENCRYPTION_KEY was rotated)."""


def encrypt_api_key(plaintext: str) -> str:
    key = get_settings().llm_encryption_key
    if not key:
        return plaintext  # dev mode: no encryption
    from cryptography.fernet import Fernet

    return Fernet(key.encode()).encrypt(plaintext.encode()).decode()


def decrypt_api_key(ciphertext: str) -> str:
    key = get_settings().llm_encryption_key
    if not key:
        return ciphertext  # dev mode
    from cryptography.fernet import Fernet, InvalidToken

    try:
        return Fernet(key.encode()).decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise KeyDecryptError("stored LLM key could not be decrypted") from exc
