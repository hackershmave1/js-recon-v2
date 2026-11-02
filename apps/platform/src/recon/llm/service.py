"""LLM config persistence — save, load, clear, and test-connectivity.

API keys are stored as Fernet ciphertext in ``session_llm_config.encrypted_api_key``.
If ``RECON_LLM_ENCRYPTION_KEY`` is empty (dev default), the key is stored in
plaintext — operators MUST set this env var in any real deployment.

All functions run synchronously (called via ``run_in_threadpool`` from the router)
to match the existing pattern for DB-touching service modules.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pydantic import BaseModel

from recon.db.base import tenant_session
from recon.db.models import SessionLlmConfig
from recon.llm import calls, tenant_config
from recon.llm.crypto import decrypt_api_key, encrypt_api_key
from recon.llm.provider import VALID_PROVIDERS
from recon.llm.run_choice import KeySource, RunChoice
from recon.sessions import service as sessions_service

# ---------------------------------------------------------------------------
# Public service functions
# ---------------------------------------------------------------------------


def save_config(
    tenant_id: str,
    session_id: str,
    provider: str,
    model: str,
    api_key: str,
) -> dict[str, Any] | None:
    """Upsert the LLM config for a session. Returns the config dict, or None if
    the session does not exist (caller maps to 404). Raises ValueError for an
    unsupported provider, and tenant_config.ProviderKeyRequired for a blank key with a
    changed provider (the router maps only the latter to 422)."""
    if provider not in VALID_PROVIDERS:
        raise ValueError(f"unsupported provider: {provider!r}")

    try:
        resolved = sessions_service.resolve_session_id(tenant_id, session_id)
    except ValueError:
        return None

    with tenant_session(tenant_id) as db:
        existing = (
            db.query(SessionLlmConfig)
            .filter_by(session_id=uuid.UUID(resolved), tenant_id=uuid.UUID(tenant_id))
            .first()
        )
        encrypted = encrypt_api_key(api_key) if api_key else None

        if existing:
            # The stored key belongs to the stored provider; never carry it to another.
            if not api_key and existing.encrypted_api_key and existing.provider != provider:
                raise tenant_config.ProviderKeyRequired
            existing.provider = provider
            existing.model = model
            if api_key:
                existing.encrypted_api_key = encrypted
            existing.configured_at = dt.datetime.now(dt.UTC)
            existing.tested_at = None
            row = existing
        else:
            row = SessionLlmConfig(
                tenant_id=uuid.UUID(tenant_id),
                session_id=uuid.UUID(resolved),
                provider=provider,
                model=model,
                encrypted_api_key=encrypted,
            )
            db.add(row)

        db.flush()
        db.refresh(row)
        return _serialize(row)


def get_config(tenant_id: str, session_id: str) -> dict[str, Any] | None:
    """Return the current LLM config for a session (key is NEVER returned).
    Returns None when no config exists yet (not a 404 — session may still exist)."""
    try:
        resolved = sessions_service.resolve_session_id(tenant_id, session_id)
    except ValueError:
        return None
    with tenant_session(tenant_id) as db:
        row = (
            db.query(SessionLlmConfig)
            .filter_by(session_id=uuid.UUID(resolved), tenant_id=uuid.UUID(tenant_id))
            .first()
        )
        return _serialize(row) if row else None


def delete_config(tenant_id: str, session_id: str) -> bool:
    """Remove the config. Returns False if nothing to delete."""
    try:
        resolved = sessions_service.resolve_session_id(tenant_id, session_id)
    except ValueError:
        return False
    with tenant_session(tenant_id) as db:
        row = (
            db.query(SessionLlmConfig)
            .filter_by(session_id=uuid.UUID(resolved), tenant_id=uuid.UUID(tenant_id))
            .first()
        )
        if row is None:
            return False
        db.delete(row)
        db.flush()
        return True


def get_test_target(tenant_id: str, session_id: str) -> tuple[str, str, str] | str:
    """``(provider, model, api_key)`` to test for a session, or an error message."""
    try:
        resolved = sessions_service.resolve_session_id(tenant_id, session_id)
    except ValueError:
        return "session not found"
    with tenant_session(tenant_id) as db:
        row = (
            db.query(SessionLlmConfig)
            .filter_by(session_id=uuid.UUID(resolved), tenant_id=uuid.UUID(tenant_id))
            .first()
        )
        if row is None:
            return "no config saved"
        if not row.encrypted_api_key:
            return "no API key stored"
        try:
            return row.provider, row.model, decrypt_api_key(row.encrypted_api_key)
        except Exception as exc:
            return f"key decryption failed: {exc}"


def mark_tested(tenant_id: str, session_id: str) -> None:
    resolved = sessions_service.resolve_session_id(tenant_id, session_id)
    with tenant_session(tenant_id) as db:
        row = (
            db.query(SessionLlmConfig)
            .filter_by(session_id=uuid.UUID(resolved), tenant_id=uuid.UUID(tenant_id))
            .first()
        )
        if row is not None:
            row.tested_at = dt.datetime.now(dt.UTC)


class _Pong(BaseModel):
    ok: bool


async def ping_credentials(
    provider_name: str,
    model: str | None,
    api_key: str,
    *,
    tenant_id: str,
    key_source: KeySource,
    session_id: str | None = None,
) -> str | None:
    """None if the key works, else the error text. Recorded as a ``key_test`` LLM call (no
    bodies: the prompt is this constant ping).

    NOTE: must be awaited on the request's own event loop. The old version ran
    get_event_loop().run_until_complete() inside run_in_threadpool, which raises
    RuntimeError in a worker thread on Python 3.11, so Test always failed."""
    try:
        await calls.generate(
            RunChoice(provider=provider_name, model=model, source=key_source, api_key=api_key),
            tenant_id=tenant_id,
            purpose="key_test",
            system_prompt='Reply with JSON: {"ok": true}',
            user_prompt="ping",
            output_schema=_Pong,
            max_tokens=16,
            session_id=session_id,
        )
    except Exception as exc:
        return str(exc)
    return None


# ---------------------------------------------------------------------------
# Serialization — key is NEVER included
# ---------------------------------------------------------------------------


def _serialize(row: SessionLlmConfig) -> dict[str, Any]:
    return {
        "provider": row.provider,
        "model": row.model,
        "has_key": bool(row.encrypted_api_key),
        "configured_at": row.configured_at.isoformat() if row.configured_at else None,
        "tested_at": row.tested_at.isoformat() if row.tested_at else None,
    }
