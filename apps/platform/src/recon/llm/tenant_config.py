"""Team-wide (tenant-level) LLM config: one provider + model + key per tenant.

Writes re-check the caller's role against app_user inside the same tenant transaction:
the login token's role can be up to auth_token_ttl_seconds (8h) stale, and the user row
must exist for the configured_by FK."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from recon.db.base import tenant_session
from recon.db.models import AppUser, TenantLlmConfig
from recon.llm.crypto import decrypt_api_key, encrypt_api_key
from recon.observability import get_logger

log = get_logger("recon.llm.team_config")

# A stored key belongs to the provider it was saved with; a blank-key save that changes
# the provider would keep, say, an OpenRouter key and send it to Anthropic.
NEW_PROVIDER_NEEDS_KEY = "a new provider needs its own API key"


class ProviderKeyRequired(ValueError):
    """A blank-key save tried to change provider. The one save error that's the caller's
    fault (routers map it to 422); any other ValueError, e.g. a malformed
    RECON_LLM_ENCRYPTION_KEY, is a server problem and must stay a 500."""

    def __init__(self) -> None:
        super().__init__(NEW_PROVIDER_NEEDS_KEY)


def _is_admin(db: Session, user_id: str) -> bool:
    role = db.execute(
        select(AppUser.role).where(AppUser.id == uuid.UUID(user_id))
    ).scalar_one_or_none()
    return role == "admin"


def _row(db: Session, tenant_id: str) -> TenantLlmConfig | None:
    return db.execute(
        select(TenantLlmConfig).where(TenantLlmConfig.tenant_id == uuid.UUID(tenant_id))
    ).scalar_one_or_none()


def _serialize(db: Session, row: TenantLlmConfig, *, include_actor: bool) -> dict[str, Any]:
    email = None
    if include_actor and row.configured_by is not None:
        email = db.execute(
            select(AppUser.email).where(AppUser.id == row.configured_by)
        ).scalar_one_or_none()
    return {
        "provider": row.provider,
        "model": row.model,
        "has_key": bool(row.encrypted_api_key),
        "configured_at": row.configured_at.isoformat() if row.configured_at else None,
        "configured_by": email,
        "tested_at": row.tested_at.isoformat() if row.tested_at else None,
    }


def is_admin(tenant_id: str, user_id: str) -> bool:
    with tenant_session(tenant_id) as db:
        return _is_admin(db, user_id)


def get_config(tenant_id: str, *, include_actor: bool) -> dict[str, Any] | None:
    with tenant_session(tenant_id) as db:
        row = _row(db, tenant_id)
        return None if row is None else _serialize(db, row, include_actor=include_actor)


def save_config(
    tenant_id: str, user_id: str, provider: str, model: str, api_key: str
) -> dict[str, Any] | None:
    """Upsert the team config. None if the caller isn't an admin (per the DB).

    Raises ProviderKeyRequired for a blank key with a changed provider."""
    with tenant_session(tenant_id) as db:
        if not _is_admin(db, user_id):
            return None
        existing = _row(db, tenant_id)
        if (
            not api_key
            and existing is not None
            and existing.encrypted_api_key
            and existing.provider != provider
        ):
            raise ProviderKeyRequired
        values: dict[str, Any] = {
            "provider": provider,
            "model": model,
            "configured_at": dt.datetime.now(dt.UTC),
            "configured_by": uuid.UUID(user_id),
            "tested_at": None,
        }
        if api_key:  # empty keeps the stored key, same contract as the session endpoint
            values["encrypted_api_key"] = encrypt_api_key(api_key)
        # ON CONFLICT so two concurrent first saves can't hit the UNIQUE(tenant_id).
        db.execute(
            insert(TenantLlmConfig)
            .values(tenant_id=uuid.UUID(tenant_id), **values)
            .on_conflict_do_update(index_elements=["tenant_id"], set_=values)
        )
        db.expire_all()
        result = _serialize(db, _row(db, tenant_id), include_actor=True)
    log.info(
        "llm.team_config.saved",
        tenant_id=tenant_id,
        user_id=user_id,
        provider=provider,
        model=model,
        key_changed=bool(api_key),
    )
    return result


def delete_config(tenant_id: str, user_id: str) -> bool | None:
    """True if deleted, False if there was nothing, None if the caller isn't an admin."""
    with tenant_session(tenant_id) as db:
        if not _is_admin(db, user_id):
            return None
        deleted = db.execute(
            delete(TenantLlmConfig).where(TenantLlmConfig.tenant_id == uuid.UUID(tenant_id))
        ).rowcount
    if deleted:
        log.info("llm.team_config.deleted", tenant_id=tenant_id, user_id=user_id)
    return bool(deleted)


def load_key(tenant_id: str) -> tuple[str, str, str] | None:
    """``(provider, model, api_key)`` if the team has a key. Raises KeyDecryptError."""
    with tenant_session(tenant_id) as db:
        row = _row(db, tenant_id)
        if row is None or not row.encrypted_api_key:
            return None
        return row.provider, row.model, decrypt_api_key(row.encrypted_api_key)


def mark_tested(tenant_id: str) -> None:
    with tenant_session(tenant_id) as db:
        db.execute(
            update(TenantLlmConfig)
            .where(TenantLlmConfig.tenant_id == uuid.UUID(tenant_id))
            .values(tested_at=dt.datetime.now(dt.UTC))
        )
