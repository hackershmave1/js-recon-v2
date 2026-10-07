"""LLM config persistence — save, load, clear, and test-connectivity.

API keys are stored as Fernet ciphertext in ``session_llm_config.encrypted_api_key``.
If ``RECON_LLM_ENCRYPTION_KEY`` is empty (dev default), the key is stored in
plaintext — operators MUST set this env var in any real deployment.

All functions run synchronously (called via ``run_in_threadpool`` from the router)
to match the existing pattern for DB-touching service modules.
"""

from __future__ import annotations

import datetime as dt
import os
import uuid
from typing import Any

from recon.db.base import tenant_session
from recon.db.models import EngagementSession, SessionLlmConfig
from recon.llm import tenant_config
from recon.llm.crypto import decrypt_api_key, encrypt_api_key
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
        resolved = _resolve_session_id(tenant_id, session_id)
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


def get_test_target(tenant_id: str, session_id: str) -> tuple[str, str, str] | str:
    """``(provider, model, api_key)`` to test for a session, or an error message."""
    try:
        resolved = _resolve_session_id(tenant_id, session_id)
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
    resolved = _resolve_session_id(tenant_id, session_id)
    with tenant_session(tenant_id) as db:
        row = (
            db.query(SessionLlmConfig)
            .filter_by(session_id=uuid.UUID(resolved), tenant_id=uuid.UUID(tenant_id))
            .first()
        )
        if row is not None:
            row.tested_at = dt.datetime.now(dt.UTC)


async def ping_credentials(provider_name: str, model: str | None, api_key: str) -> str | None:
    """None if the key works, else the error text.

    NOTE: must be awaited on the request's own event loop. The old version ran
    get_event_loop().run_until_complete() inside run_in_threadpool, which raises
    RuntimeError in a worker thread on Python 3.11, so Test always failed."""
    try:
        await _ping(build_provider(provider_name, api_key=api_key, model=model))
    except Exception as exc:
        return str(exc)
    return None


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


# Operator-wide keys, tried in order when a session has no saved key. Each is bound to the
# provider it belongs to: the key and the provider must come from the same lookup, or an
# OpenRouter key ends up sent to Anthropic.
_ENV_KEYS = (("openrouter", "OPENROUTER_API_KEY"), ("anthropic", "ANTHROPIC_API_KEY"))


def load_credentials(tenant_id: str, session_id: str) -> tuple[str, str | None, str] | None:
    """``(provider, model, api_key)`` for internal use (threat model generation), or None.

    A key saved on the session wins, then the team key (Settings), then an operator-wide
    env key, each with its own provider. Never exposed via the HTTP API."""
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
        if row is not None and row.encrypted_api_key:
            return row.provider, row.model, decrypt_api_key(row.encrypted_api_key)
        saved_provider, saved_model = (row.provider, row.model) if row else (None, None)
    # Team key next: it carries its own provider + model, never the session's.
    team = tenant_config.load_key(tenant_id)
    if team is not None:
        return team
    for provider, env_var in _ENV_KEYS:
        if api_key := os.environ.get(env_var):
            # Model ids are provider-specific, so a saved model only carries over to its own
            # provider; otherwise build_provider falls back to that provider's default.
            return provider, saved_model if saved_provider == provider else None, api_key
    return None


def peek_credential_provider(tenant_id: str, session_id: str) -> str | None:
    """Which provider load_credentials would use, without decrypting anything (for the UI)."""
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
        if row is not None and row.encrypted_api_key:
            return row.provider
    team = tenant_config.team_key_provider(tenant_id)
    if team is not None:
        return team
    for provider, env_var in _ENV_KEYS:
        if os.environ.get(env_var):
            return provider
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
