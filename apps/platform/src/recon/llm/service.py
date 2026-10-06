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

from recon.config import get_settings
from recon.db.base import tenant_session
from recon.db.models import EngagementSession, SessionLlmConfig
from recon.llm.provider import VALID_PROVIDERS, build_provider
from recon.sessions import service as sessions_service


def _resolve_session_id(tenant_id: str, session_id: str) -> str:
    """Accept either a platform UUID or the extension's external_id (same fallback
    pattern as sessions_router). Returns the platform UUID, or raises ValueError."""
    try:
        uuid.UUID(session_id)  # validate format; raises ValueError if not a UUID
        # tenant_session sets app.current_tenant, which the RLS policy filters on.
        with tenant_session(tenant_id) as db:
            row = db.query(EngagementSession).filter_by(id=uuid.UUID(session_id)).first()
            if row is not None:
                return session_id
    except (ValueError, AttributeError):
        pass  # not a UUID — fall through to external_id lookup
    platform_id = sessions_service.find_session_id_by_external_id(tenant_id, session_id)
    if platform_id is None:
        raise ValueError("session not found")
    return platform_id


# ---------------------------------------------------------------------------
# Encryption helpers
# ---------------------------------------------------------------------------


def _encrypt(plaintext: str) -> str:
    key = get_settings().llm_encryption_key
    if not key:
        return plaintext  # dev mode — no encryption
    from cryptography.fernet import Fernet

    return Fernet(key.encode()).encrypt(plaintext.encode()).decode()


def _decrypt(ciphertext: str) -> str:
    key = get_settings().llm_encryption_key
    if not key:
        return ciphertext  # dev mode
    from cryptography.fernet import Fernet

    return Fernet(key.encode()).decrypt(ciphertext.encode()).decode()


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
    the session does not exist (caller maps to 404)."""
    if provider not in VALID_PROVIDERS:
        raise ValueError(f"unsupported provider: {provider!r}")

    try:
        resolved = _resolve_session_id(tenant_id, session_id)
    except ValueError:
        return None

    with tenant_session(tenant_id) as db:
        existing = (
            db.query(SessionLlmConfig)
            .filter_by(session_id=uuid.UUID(resolved), tenant_id=uuid.UUID(tenant_id))
            .first()
        )
        encrypted = _encrypt(api_key) if api_key else None

        if existing:
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
        resolved = _resolve_session_id(tenant_id, session_id)
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
        resolved = _resolve_session_id(tenant_id, session_id)
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


def test_config(tenant_id: str, session_id: str) -> dict[str, Any]:
    """Fire a minimal API call to verify the stored key works.

    Returns ``{"ok": True}`` on success, ``{"ok": False, "error": "..."}`` on
    any failure. Stamps ``tested_at`` on success."""
    try:
        resolved = _resolve_session_id(tenant_id, session_id)
    except ValueError:
        return {"ok": False, "error": "session not found"}
    with tenant_session(tenant_id) as db:
        row = (
            db.query(SessionLlmConfig)
            .filter_by(session_id=uuid.UUID(resolved), tenant_id=uuid.UUID(tenant_id))
            .first()
        )
        if row is None:
            return {"ok": False, "error": "no config saved"}
        if not row.encrypted_api_key:
            return {"ok": False, "error": "no API key stored"}

        try:
            api_key = _decrypt(row.encrypted_api_key)
        except Exception as exc:
            return {"ok": False, "error": f"key decryption failed: {exc}"}

        try:
            import asyncio

            provider = build_provider(row.provider, api_key=api_key, model=row.model)
            asyncio.get_event_loop().run_until_complete(_ping(provider))
            row.tested_at = dt.datetime.now(dt.UTC)
            db.flush()
            return {"ok": True, "provider": row.provider, "model": row.model}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}


async def _ping(provider) -> None:
    """Minimal call to verify the key is accepted — single short message."""
    from pydantic import BaseModel

    class _Pong(BaseModel):
        ok: bool

    await provider.generate_structured(
        system_prompt='Reply with JSON: {"ok": true}',
        user_prompt="ping",
        output_schema=_Pong,
        max_tokens=16,
    )


def load_api_key(tenant_id: str, session_id: str) -> str | None:
    """Decrypt and return the API key for internal use (threat model generation).
    Never exposed via the HTTP API."""
    try:
        resolved = _resolve_session_id(tenant_id, session_id)
    except ValueError:
        return None
    with tenant_session(tenant_id) as db:
        row = (
            db.query(SessionLlmConfig)
            .filter_by(session_id=uuid.UUID(resolved), tenant_id=uuid.UUID(tenant_id))
            .first()
        )
        if row is None or not row.encrypted_api_key:
            return None
        return _decrypt(row.encrypted_api_key)


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
